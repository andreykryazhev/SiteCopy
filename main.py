import os
import argparse
import traceback
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin, urlunparse

import requests


root_site_name = ''
root_path = ''
registered_tag_handlers = {}
processed = set()
args = None


class URL:

    def __init__(self, url):
        self.url = url.strip()
        self.scheme, netloc, path, self.params, _, _ = urlparse(self.url)
        self.netloc = self.normalize_netloc(netloc)
        self.path = self.normalize_path(path)

    @staticmethod
    def normalize_netloc(value):
        if not value:
            return value
        if value.endswith('/'):
            value = value[:-1]
        if not value.startswith('www.'):
            value = 'www.' + value
        return value

    @staticmethod
    def normalize_path(value):
        if value.endswith('/'):
            value = value[:-1]
        #if value.startswith('/'):
        #    value = value[1:]
        return value

    @property
    def filename(self):
        """ Вытаскивает имя файла из URL-пути

        Если URL-путь не содержит имени файла, то возвращается None.
        """
        if not self.path:
            return None
        path, last_chunk = os.path.split(self.path)
        if not last_chunk:
            return None
        filename, ext = os.path.splitext(last_chunk)
        if ext:
            # если присутствует расширение, то значит это имя файла
            return last_chunk
        return None

    @property
    def is_absolute(self):
        """Флаг абсолютной ссылки"""
        return bool(self.netloc)

    @property
    def is_relative(self):
        """Флаг относительной ссылки"""
        return not self.is_absolute

    def get_local_path(self, base_url):

        def convert_url_path_to_local(url_path):
            result = url_path.replace('/', os.path.sep)
            if result.startswith(os.path.sep):
                result = result[len(os.path.sep):]
            if result.endswith(os.path.sep):
                result = result[: -len(os.path.sep)]
            return result

        if self.is_absolute:
            if not self.path:
                # 'http://www.abc.com'
                return root_path, 'index.html'
            else:
                # www.abc.qwerty\foo\bar\
                # www.abc.qwerty\foo\bar\index.html
                path = convert_url_path_to_local(self.path)
                path = path.split(os.path.sep)
                if self.filename:
                    path = path[:-1]
                return os.path.join(root_path, *path), self.filename or 'index.html'

        elif self.is_relative:
            if self.path.startswith('/'):
                path = convert_url_path_to_local(self.path)
                return os.path.join(root_path, path), self.filename or 'index.html'
            else:
                if '..' in self.path:

                    # todo: предполагается что относительный путь всегда заканчивается именем файла
                    path, filename = os.path.split(self.path)
                    count_to_up = self.path.count('..')
                    path = path.split('/')
                    #if self.filename:
                    #    path = path[:-1]
                    path = path[count_to_up:]

                    base_url = URL(base_url)
                    base_path = convert_url_path_to_local(base_url.path)
                    base_path = base_path.split(os.path.sep)
                    if base_url.filename:
                        base_path = base_path[:-1]
                    base_path = base_path[:-count_to_up]
                    return os.path.join(root_path, *base_path, *path), self.filename or 'index.html'

                else:
                    base_path = convert_url_path_to_local(URL(base_url).path)
                    add_path = convert_url_path_to_local(self.path)
                    add_path = add_path.split(os.path.sep)
                    if self.filename:
                        add_path = add_path[:-1]
                    return os.path.join(root_path, base_path, *add_path), self.filename or 'index.html'

    @property
    def original_url(self):
        return self.url

    @property
    def normalized_url(self):
        return urlunparse((self.scheme, self.netloc, self.path, self.params, None, None))

    def is_link_to_another_site(self, link):
        parse_result = urlparse(link)
        netloc = self.normalize_netloc(parse_result.netloc.strip())
        if not netloc:
            return False
        return netloc != self.netloc

    def __eq__(self, other):
        if not isinstance(other, URL):
            return NotImplemented
        if self.is_absolute:
            if self.netloc == other.netloc and self.path == other.path:
                return True
        elif self.is_relative:
            if self.path == other.path:
                return True
        return False

    def __repr__(self):
        return '<URL, netloc: "{}", path: "{}">'.format(self.netloc, self.path)


def handle_link(value, base_url):
    url = URL(value)
    if url.is_absolute:
        if url.is_link_to_another_site(base_url):
            # ссылка на другой сайт
            return value

        if url == URL(base_url):
            # ссылка на самого себя
            path, filename = url.get_local_path(base_url)
            return os.path.join(path, filename)
        else:
            # http://www.abc.com/qwerty
            # http://www.abc.com/qwerty/index.html
            path, filename = url.get_local_path(base_url)
            full_name = os.path.join(path, filename)
            if full_name not in processed:
                download(base_url, url.original_url)
            return full_name

    elif url.is_relative:
        # qwerty/bar
        # ../../qwerty/bar
        path, filename = url.get_local_path(base_url)
        new_url = urljoin(base_url, url.path)
        full_name = os.path.join(path, filename)
        if full_name not in processed:
            download(base_url, new_url)
        return full_name


def handle_tag_with_href(attrs, base_url, url):
    result = []
    for attr, value in attrs:

        if not value or not value.strip():
            result.append((attr, value))
            continue

        if attr.lower() == 'href':
            result.append((attr, handle_link(value, base_url)))
        else:
            result.append((attr, value))
    return result


class CustomHtmlParser(HTMLParser):

    def __init__(self, base_url, url, *args, **kwargs):
        self.base_url = base_url
        self.url = url
        super().__init__(*args, **kwargs)
        self.as_string = ''

    def handle_decl(self, decl):
        self.as_string += '<!' + decl + '>'

    def handle_comment(self, data):
        self.as_string += data

    def handle_data(self, data):
        self.as_string += data

    def handle_starttag(self, tag, attrs):
        handler = registered_tag_handlers.get(tag, None)
        if handler:
            attrs = handler(attrs, self.base_url, self.url)
        attrs = ' '.join(['{}="{}"'.format(attr, value) for attr, value in attrs])
        attrs = attrs.strip()
        self.as_string += '<{}'.format(tag)
        if attrs:
            self.as_string += ' ' + attrs
        self.as_string += '>'

    def handle_endtag(self, tag):
        self.as_string += '</{}>'.format(tag)


def question(url, path):
    print('-' * 80)
    print('Wanted to download "{}" to "{}"'.format(url, path))
    answer = input('Processed?')
    return answer.lower() in ('y', 'yes')


def download(base_url, url):
    """Основная функция загрузки

    base_url - ссылка на обрабатываемый файл
    url - текущая обрабатываемая ссылка
    """

    if base_url is None:
        path, filename = root_path, 'index.html'
    else:
        path, filename = URL(url).get_local_path(base_url)

    full_name = os.path.normpath(os.path.join(path, filename))
    processed.add(full_name)

    if args.confirmation and not question(url, full_name):
        print('Skipping...')
        return

    os.makedirs(path, exist_ok=True)

    try:
        result = requests.get(url)
        if result.status_code != 200:
            print('Bad error code [{}] for url: "{}"'.format(url, result.status_code))
            return

        site_content = result.content

        if os.path.splitext(filename)[1] in ('.html', '.htm'):
            parser = CustomHtmlParser(base_url or url, url)
            parser.feed(site_content.decode('utf-8', errors='ignore'))
            parser.close()
            site_content = parser.as_string
        else:
            site_content = site_content.decode('utf-8', errors='ignore')

        if args.only_update and os.path.exists(full_name):
            print('Skipping "{}" since it already exists ("only-update" option is specified)'
                  .format(full_name))
            return

        with open(full_name, 'w', encoding='utf-8') as f:
            f.write(site_content)
            print('Processed: {}'.format(os.path.join(path, filename)))

    except Exception:
        print('Unknown exception!')
        print(traceback.format_exc())


def test():

    global root_path
    root_path = 'c:\\test'

    assert URL('http://www.abc.com/qwerty').is_absolute
    assert URL('/qwerty/foo').is_relative
    assert URL('http://www.abc.com/qwerty') == URL('http://www.abc.com/qwerty/')
    assert URL('http://www.abc.com/qwerty/foo') != URL('http://www.abc.com/qwerty')
    assert not URL('http://www.abc.com/qwerty').is_link_to_another_site('example.html')
    assert not URL('http://www.abc.com/qwerty/foo').is_link_to_another_site(
        'http://www.abc.com/qwerty/bar'
    )
    assert URL('http://www.abc.com/qwerty/foo').is_link_to_another_site(
        'http://www.bla-bla.com/qwerty/bar'
    )

    # calculate_local_path
    assert URL('http://www.abc.com/qwerty/foo').get_local_path('http://www.abc.com') == \
        ('c:\\test\\qwerty\\foo', 'index.html')
    assert URL('http://www.abc.com/qwerty/foo/example.html').get_local_path('http://www.abc.com') == \
        ('c:\\test\\qwerty\\foo', 'example.html')
    assert URL('qwerty/foo').get_local_path('http://www.abc.com') == \
        ('c:\\test\\qwerty\\foo', 'index.html')
    assert URL('qwerty/foo/example.html').get_local_path('http://www.abc.com') == \
        ('c:\\test\\qwerty\\foo', 'example.html')
    assert URL('/qwerty/foo/example.html').get_local_path('http://www.abc.com/spam') == \
        ('c:\\test\\spam\\qwerty\\foo', 'example.html')
    assert URL('../foo/example.html').get_local_path('http://www.abc.com/spam/index.html') == \
        ('c:\\test\\foo', 'example.html')

    assert URL('http://abc.com/qwerty').normalized_url == 'http://www.abc.com/qwerty'
    print('Tests execution successfully completed.')


def main():
    global root_site_name
    global root_path

    root_site_name = URL(args.url).normalized_url
    root_path = os.path.abspath(args.path)
    download(None, root_site_name)


if __name__ == '__main__':
    registered_tag_handlers['a'] = handle_tag_with_href
    registered_tag_handlers['link'] = handle_tag_with_href

    parser = argparse.ArgumentParser()
    parser.add_argument('url', type=str, help='The main URL that you want to copy.')
    parser.add_argument('path', type=str, help='Location for copied files')
    parser.add_argument('--only-update', action='store_true',
                        help='Copy only new files and skip files that already existed')
    parser.add_argument('--confirmation', action='store_true', help='Ask confirmation before each copy')
    args = parser.parse_args()

    test()
    main()
    print('Script execution completed successfully.')

