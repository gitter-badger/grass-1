#!/usr/bin/env python3

############################################################################
#
# MODULE:       Builds manual pages
# AUTHOR(S):    Markus Neteler
#               Glynn Clements
#               Martin Landa <landa.martin gmail.com>
# PURPOSE:      Create HTML manual page snippets
# COPYRIGHT:    (C) 2007-2022 by Glynn Clements
#                and the GRASS Development Team
#
#               This program is free software under the GNU General
#               Public License (>=v2). Read the file COPYING that
#               comes with GRASS for details.
#
#############################################################################

import http
import sys
import os
import string
import re
from datetime import datetime
import locale
import json
import pathlib
import subprocess
import time

try:
    # Python 2 import
    from HTMLParser import HTMLParser
except ImportError:
    # Python 3 import
    from html.parser import HTMLParser

from six.moves.urllib import request as urlrequest
from six.moves.urllib.error import HTTPError, URLError

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

try:
    import grass.script as gs
except ImportError:
    # During compilation GRASS GIS
    gs = None

from generate_last_commit_file import COMMIT_DATE_FORMAT

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}
HTTP_STATUS_CODES = list(http.HTTPStatus)

if sys.version_info[0] == 2:
    PY2 = True
else:
    PY2 = False


if not PY2:
    unicode = str


grass_version = os.getenv("VERSION_NUMBER", "unknown")
trunk_url = ""
addons_url = ""
grass_git_branch = "main"
if grass_version != "unknown":
    major, minor, patch = grass_version.split(".")
    base_url = "https://github.com/OSGeo"
    trunk_url = "{base_url}/grass/tree/{branch}/".format(
        base_url=base_url, branch=grass_git_branch
    )
    addons_url = "{base_url}/grass-addons/tree/grass{major}/".format(
        base_url=base_url, major=major
    )


def _get_encoding():
    encoding = locale.getdefaultlocale()[1]
    if not encoding:
        encoding = "UTF-8"
    return encoding


def decode(bytes_):
    """Decode bytes with default locale and return (unicode) string

    No-op if parameter is not bytes (assumed unicode string).

    :param bytes bytes_: the bytes to decode
    """
    if isinstance(bytes_, unicode):
        return bytes_
    if isinstance(bytes_, bytes):
        enc = _get_encoding()
        return bytes_.decode(enc)
    return unicode(bytes_)


def urlopen(url, *args, **kwargs):
    """Wrapper around urlopen. Same function as 'urlopen', but with the
    ability to define headers.
    """
    request = urlrequest.Request(url, headers=HEADERS)
    return urlrequest.urlopen(request, *args, **kwargs)


def set_proxy():
    """Set proxy"""
    proxy = os.getenv("GRASS_PROXY")
    if proxy:
        proxies = {}
        for ptype, purl in (p.split("=") for p in proxy.split(",")):
            proxies[ptype] = purl
        urlrequest.install_opener(
            urlrequest.build_opener(urlrequest.ProxyHandler(proxies))
        )


set_proxy()


def download_git_commit(url, response_format, *args, **kwargs):
    """Download module/addon last commit from GitHub API

    :param str url: url address
    :param str response_format: content type

    :return urllib.request.urlopen or None response: response object or
                                                     None
    """
    try:
        response = urlopen(url, *args, **kwargs)
        if not response.code == 200:
            index = HTTP_STATUS_CODES.index(response.code)
            desc = HTTP_STATUS_CODES[index].description
            gs.fatal(
                _(
                    "Download commit from <{url}>, return status code "
                    "{code}, {desc}".format(
                        url=url,
                        code=response.code,
                        desc=desc,
                    ),
                ),
            )
        if response_format not in response.getheader("Content-Type"):
            gs.fatal(
                _(
                    "Wrong downloaded commit file format. "
                    "Check url <{url}>. Allowed file format is "
                    "{response_format}.".format(
                        url=url,
                        response_format=response_format,
                    ),
                ),
            )
        return response
    except HTTPError as err:
        gs.warning(
            _(
                "The download of the commit from the GitHub API "
                "server wasn't successful, <{}>. Commit and commit "
                "date will not be included in the <{}> addon html manual "
                "page.".format(err.msg, pgm)
            ),
        )
    except URLError:
        gs.warning(
            _(
                "Download file from <{url}>, failed. Check internet "
                "connection. Commit and commit date will not be included "
                "in the <{pgm}> addon manual page.".format(url=url, pgm=pgm)
            ),
        )


def get_default_git_log(src_dir):
    """Get default Git commit and commit date, when getting commit from
    local Git, local JSON file and remote GitHub REST API server wasn't
    successfull.

    :param str src_dir: addon source dir

    :return dict: dict which store last commit and commnit date
    """
    return {
        "commit": "unknown",
        "date": time.ctime(os.path.getmtime(src_dir)),
    }


def parse_git_commit(
    commit,
    src_dir,
    git_log=None,
):
    """Parse Git commit

    :param str commit: commit message
    :param str src_dir: addon source dir
    :param dict git_log: dict which store last commit and commnit
                         date

    :return dict git_log: dict which store last commit and commnit date
    """
    if not git_log:
        git_log = get_default_git_log(src_dir=src_dir)
    if commit:
        git_log["commit"], commit_date = commit.strip().split(",")
        git_log["date"] = format_git_commit_date_from_local_git(
            commit_datetime=commit_date,
        )
    return git_log


def get_git_commit_from_file(
    src_dir,
    git_log=None,
):
    """Get Git commit from JSON file

    :param str src_dir: addon source dir
    :param dict git_log: dict which store last commit and commnit date

    :return dict git_log: dict which store last commit and commnit date
    """
    # Accessed date time if getting commit from JSON file wasn't successfull
    if not git_log:
        git_log = get_default_git_log(src_dir=src_dir)
    json_file_path = os.path.join(
        topdir,
        "core_modules_with_last_commit.json",
    )
    if os.path.exists(json_file_path):
        with open(json_file_path) as f:
            core_modules_with_last_commit = json.load(f)
        if pgm in core_modules_with_last_commit:
            core_module = core_modules_with_last_commit[pgm]
            git_log["commit"] = core_module["commit"]
            git_log["date"] = format_git_commit_date_from_local_git(
                commit_datetime=core_module["date"],
            )
    return git_log


def get_git_commit_from_rest_api_for_addon_repo(
    addon_path,
    src_dir,
    git_log=None,
):
    """Get Git commit from remote GitHub REST API for addon repository

    :param str addon_path: addon path
    :param str src_dir: addon source dir
    :param dict git_log: dict which store last commit and commnit date

    :return dict git_log: dict which store last commit and commnit date
    """
    # Accessed date time if getting commit from GitHub REST API wasn't successfull
    if not git_log:
        git_log = get_default_git_log(src_dir=src_dir)
    grass_addons_url = (
        "https://api.github.com/repos/osgeo/grass-addons/commits?"
        "path={path}&page=1&per_page=1&sha=grass{major}".format(
            path=addon_path,
            major=major,
        )
    )  # sha=git_branch_name

    response = download_git_commit(
        url=grass_addons_url,
        response_format="application/json",
    )
    if response:
        commit = json.loads(response.read())
        if commit:
            git_log["commit"] = commit[0]["sha"]
            git_log["date"] = format_git_commit_date_from_rest_api(
                commit_datetime=commit[0]["commit"]["author"]["date"],
            )
    return git_log


def format_git_commit_date_from_rest_api(
    commit_datetime, datetime_format="%A %b %d %H:%M:%S %Y"
):
    """Format datetime from remote GitHub REST API

    :param str commit_datetime: commit datetime
    :param str datetime_format: output commit datetime format
                                e.g. Sun Jan 16 23:09:35 2022

    :return str: output formatted commit datetime
    """
    return datetime.strptime(
        commit_datetime,
        "%Y-%m-%dT%H:%M:%SZ",  # ISO 8601 YYYY-MM-DDTHH:MM:SSZ
    ).strftime(datetime_format)


def format_git_commit_date_from_local_git(
    commit_datetime, datetime_format="%A %b %d %H:%M:%S %Y"
):
    """Format datetime from local Git or JSON file

    :param str commit_datetime: commit datetime
    :param str datetime_format: output commit datetime format
                                e.g. Sun Jan 16 23:09:35 2022

    :return str: output formatted commit datetime
    """
    return datetime.fromisoformat(
        commit_datetime,
    ).strftime(datetime_format)


def has_src_code_git(src_dir, is_addon):
    """Has core module or addon source code Git

    :param str src_dir: core module or addon root directory
    :param bool is_addon: True if it is addon

    :return subprocess.CompletedProcess or None: subprocess.CompletedProcess
                                                 if core module or addon
                                                 source code has Git
    """
    actual_dir = os.getcwd()
    if is_addon:
        os.chdir(src_dir)
    else:
        os.chdir(topdir)
    try:

        process_result = subprocess.run(
            [
                "git",
                "log",
                "-1",
                f"--format=%H,{COMMIT_DATE_FORMAT}",
                src_dir,
            ],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )  # --format=%H,COMMIT_DATE_FORMAT commit hash,author date
        os.chdir(actual_dir)
        return process_result if process_result.returncode == 0 else None
    except FileNotFoundError:
        os.chdir(actual_dir)
        return None


def get_last_git_commit(src_dir, addon_path, is_addon):
    """Get last module/addon git commit

    :param str src_dir: module/addon source dir
    :param str addon_path: addon path
    :param bool is_addon: True if it is addon

    :return dict git_log: dict with key commit and date, if not
                          possible download commit from GitHub REST API
                          server values of keys have "unknown" string
    """
    process_result = has_src_code_git(src_dir=src_dir, is_addon=is_addon)
    if process_result:
        return parse_git_commit(
            commit=process_result.stdout.decode(),
            src_dir=src_dir,
        )
    else:
        if gs:
            # Addons installation
            return get_git_commit_from_rest_api_for_addon_repo(
                addon_path=addon_path,
                src_dir=src_dir,
            )
        # During GRASS GIS compilation from source code without Git
        else:
            return get_git_commit_from_file(src_dir=src_dir)


html_page_footer_pages_path = (
    os.getenv("HTML_PAGE_FOOTER_PAGES_PATH")
    if os.getenv("HTML_PAGE_FOOTER_PAGES_PATH")
    else ""
)

pgm = sys.argv[1]

src_file = "%s.html" % pgm
tmp_file = "%s.tmp.html" % pgm

header_base = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
 <meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
 <title>${PGM} - GRASS GIS Manual</title>
 <meta name="Author" content="GRASS Development Team">
 <meta name="description" content="${PGM}: ${PGM_DESC}">
 <link rel="stylesheet" href="grassdocs.css" type="text/css">
</head>
<body bgcolor="white">
<div id="container">

<a href="index.html"><img src="grass_logo.png" alt="GRASS logo"></a>
<hr class="header">
"""

header_nopgm = """<h2>${PGM}</h2>
"""

header_pgm = """<h2>NAME</h2>
<em><b>${PGM}</b></em>
"""

header_pgm_desc = """<h2>NAME</h2>
<em><b>${PGM}</b></em> - ${PGM_DESC}
"""

sourcecode = string.Template(
    """<h2>SOURCE CODE</h2>
<p>
  Available at:
  <a href="${URL_SOURCE}">${PGM} source code</a>
  (<a href="${URL_LOG}">history</a>)
</p>
<p>
  ${DATE_TAG}
</p>
"""
)

footer_index = string.Template(
    """<hr class="header">
<p>
<a href="index.html">Main index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}${INDEXNAME}.html">${INDEXNAMECAP} index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}topics.html">Topics index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}keywords.html">Keywords index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}graphical_index.html">Graphical index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}full_index.html">Full index</a>
</p>
<p>
&copy; 2003-${YEAR}
<a href="https://grass.osgeo.org">GRASS Development Team</a>,
GRASS GIS ${GRASS_VERSION} Reference Manual
</p>

</div>
</body>
</html>
"""
)

footer_noindex = string.Template(
    """<hr class="header">
<p>
<a href="index.html">Main index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}topics.html">Topics index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}keywords.html">Keywords index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}graphical_index.html">Graphical index</a> |
<a href="${HTML_PAGE_FOOTER_PAGES_PATH}full_index.html">Full index</a>
</p>
<p>
&copy; 2003-${YEAR}
<a href="https://grass.osgeo.org">GRASS Development Team</a>,
GRASS GIS ${GRASS_VERSION} Reference Manual
</p>

</div>
</body>
</html>
"""
)


def read_file(name):
    try:
        f = open(name, "rb")
        s = f.read()
        f.close()
        if PY2:
            return s
        else:
            return decode(s)
    except IOError:
        return ""


def create_toc(src_data):
    class MyHTMLParser(HTMLParser):
        def __init__(self):
            HTMLParser.__init__(self)
            self.reset()
            self.idx = 1
            self.tag_curr = ""
            self.tag_last = ""
            self.process_text = False
            self.data = []
            self.tags_allowed = ("h1", "h2", "h3")
            self.tags_ignored = "img"
            self.text = ""

        def handle_starttag(self, tag, attrs):
            if tag in self.tags_allowed:
                self.process_text = True
            self.tag_last = self.tag_curr
            self.tag_curr = tag

        def handle_endtag(self, tag):
            if tag in self.tags_allowed:
                self.data.append((tag, "%s_%d" % (tag, self.idx), self.text))
                self.idx += 1
                self.process_text = False
                self.text = ""

            self.tag_curr = self.tag_last

        def handle_data(self, data):
            if not self.process_text:
                return
            if self.tag_curr in self.tags_allowed or self.tag_curr in self.tags_ignored:
                self.text += data
            else:
                self.text += "<%s>%s</%s>" % (self.tag_curr, data, self.tag_curr)

    # instantiate the parser and fed it some HTML
    parser = MyHTMLParser()
    parser.feed(src_data)

    return parser.data


def escape_href(label):
    # remove html tags
    label = re.sub("<[^<]+?>", "", label)
    # fix &nbsp;
    label = label.replace("&nbsp;", "")
    # fix "
    label = label.replace('"', "")
    # replace space with underscore + lower
    return label.replace(" ", "-").lower()


def write_toc(data):
    if not data:
        return

    fd = sys.stdout
    fd.write('<div class="toc">\n')
    fd.write('<h4 class="toc">Table of contents</h4>\n')
    fd.write('<ul class="toc">\n')
    first = True
    has_h2 = False
    in_h3 = False
    indent = 4
    for tag, href, text in data:
        if tag == "h3" and not in_h3 and has_h2:
            fd.write('\n%s<ul class="toc">\n' % (" " * indent))
            indent += 4
            in_h3 = True
        elif not first:
            fd.write("</li>\n")

        if tag == "h2":
            has_h2 = True
            if in_h3:
                indent -= 4
                fd.write("%s</ul></li>\n" % (" " * indent))
                in_h3 = False

        text = text.replace("\xa0", " ")
        fd.write(
            '%s<li class="toc"><a href="#%s" class="toc">%s</a>'
            % (" " * indent, escape_href(text), text)
        )
        first = False

    fd.write("</li>\n</ul>\n")
    fd.write("</div>\n")


def update_toc(data):
    ret_data = []
    pat = re.compile(r"(<(h[2|3])>)(.+)(</h[2|3]>)")
    idx = 1
    for line in data.splitlines():
        if pat.search(line):
            xline = pat.split(line)
            line = (
                xline[1]
                + '<a name="%s">' % escape_href(xline[3])
                + xline[3]
                + "</a>"
                + xline[4]
            )
            idx += 1
        ret_data.append(line)

    return "\n".join(ret_data)


def get_addon_path():
    """Check if pgm is in the addons list and get addon path

    return: pgm path if pgm is addon else None
    """
    addon_base = os.getenv("GRASS_ADDON_BASE")
    if addon_base:
        # addons_paths.json is file created during install extension
        # check get_addons_paths() function in the g.extension.py file
        addons_file = "addons_paths.json"
        addons_paths = os.path.join(addon_base, addons_file)
        if not os.path.exists(addons_paths):
            # Compiled addon has own dir e.g. ~/.grass8/addons/db.join/
            # with bin/ docs/ etc/ scripts/ subdir, required for compilation
            # addons on osgeo lxd container server and generation of
            # modules.xml file (build-xml.py script), when addons_paths.json
            # file is stored one level dir up
            addons_paths = os.path.join(
                os.path.abspath(os.path.join(addon_base, "..")),
                addons_file,
            )
            if not os.path.exists(addons_paths):
                return
        with open(addons_paths) as f:
            addons_paths = json.load(f)
        for addon in addons_paths["tree"]:
            if pgm == pathlib.Path(addon["path"]).name:
                return addon["path"]


# process header
src_data = read_file(src_file)
name = re.search("(<!-- meta page name:)(.*)(-->)", src_data, re.IGNORECASE)
pgm_desc = "GRASS GIS Reference Manual"
if name:
    pgm = name.group(2).strip().split("-", 1)[0].strip()
    name_desc = re.search(
        "(<!-- meta page name description:)(.*)(-->)", src_data, re.IGNORECASE
    )
    if name_desc:
        pgm_desc = name_desc.group(2).strip()
desc = re.search("(<!-- meta page description:)(.*)(-->)", src_data, re.IGNORECASE)
if desc:
    pgm = desc.group(2).strip()
    header_tmpl = string.Template(header_base + header_nopgm)
else:
    if not pgm_desc:
        header_tmpl = string.Template(header_base + header_pgm)
    else:
        header_tmpl = string.Template(header_base + header_pgm_desc)

if not re.search("<html>", src_data, re.IGNORECASE):
    tmp_data = read_file(tmp_file)
    """
    Adjusting keywords html pages paths if add-on html man page
    stored on the server
    """
    if html_page_footer_pages_path:
        new_keywords_paths = []
        orig_keywords_paths = re.search(
            r"<h[1-9]>KEYWORDS</h[1-9]>(.*?)<h[1-9]>",
            tmp_data,
            re.DOTALL,
        )
        if orig_keywords_paths:
            search_txt = 'href="'
            for i in orig_keywords_paths.group(1).split(","):
                if search_txt in i:
                    index = i.index(search_txt) + len(search_txt)
                    new_keywords_paths.append(
                        i[:index] + html_page_footer_pages_path + i[index:],
                    )
        if new_keywords_paths:
            tmp_data = tmp_data.replace(
                orig_keywords_paths.group(1),
                ",".join(new_keywords_paths),
            )
    if not re.search("<html>", tmp_data, re.IGNORECASE):
        sys.stdout.write(header_tmpl.substitute(PGM=pgm, PGM_DESC=pgm_desc))
    if tmp_data:
        for line in tmp_data.splitlines(True):
            if not re.search("</body>|</html>", line, re.IGNORECASE):
                sys.stdout.write(line)

# create TOC
write_toc(create_toc(src_data))

# process body
sys.stdout.write(update_toc(src_data))

# if </html> is found, suppose a complete html is provided.
# otherwise, generate module class reference:
if re.search("</html>", src_data, re.IGNORECASE):
    sys.exit()

index_names = {
    "d": "display",
    "db": "database",
    "g": "general",
    "i": "imagery",
    "m": "miscellaneous",
    "ps": "postscript",
    "p": "paint",
    "r": "raster",
    "r3": "raster3d",
    "s": "sites",
    "t": "temporal",
    "v": "vector",
}


def to_title(name):
    """Convert name of command class/family to form suitable for title"""
    if name == "raster3d":
        return "3D raster"
    elif name == "postscript":
        return "PostScript"
    else:
        return name.capitalize()


index_titles = {}
for key, name in index_names.items():
    index_titles[key] = to_title(name)

# process footer
index = re.search("(<!-- meta page index:)(.*)(-->)", src_data, re.IGNORECASE)
if index:
    index_name = index.group(2).strip()
    if "|" in index_name:
        index_name, index_name_cap = index_name.split("|", 1)
    else:
        index_name_cap = to_title(index_name)
else:
    mod_class = pgm.split(".", 1)[0]
    index_name = index_names.get(mod_class, "")
    index_name_cap = index_titles.get(mod_class, "")

year = os.getenv("VERSION_DATE")
if not year:
    year = str(datetime.now().year)

# check the names of scripts to assign the right folder
topdir = os.path.abspath(os.getenv("MODULE_TOPDIR"))
curdir = os.path.abspath(os.path.curdir)
if curdir.startswith(topdir + os.path.sep):
    source_url = trunk_url
    pgmdir = curdir.replace(topdir, "").lstrip(os.path.sep)
else:
    # addons
    source_url = addons_url
    pgmdir = os.path.sep.join(curdir.split(os.path.sep)[-3:])
url_source = ""
addon_path = None
if os.getenv("SOURCE_URL", ""):
    addon_path = get_addon_path()
    if addon_path:
        # Addon is installed from the local dir
        if os.path.exists(os.getenv("SOURCE_URL")):
            url_source = urlparse.urljoin(
                addons_url,
                addon_path,
            )
        else:
            url_source = urlparse.urljoin(
                os.environ["SOURCE_URL"].split("src")[0],
                addon_path,
            )
else:
    url_source = urlparse.urljoin(source_url, pgmdir)
if sys.platform == "win32":
    url_source = url_source.replace(os.path.sep, "/")

if index_name:
    branches = "branches"
    tree = "tree"
    commits = "commits"

    if branches in url_source:
        url_log = url_source.replace(branches, commits)
        url_source = url_source.replace(branches, tree)
    else:
        url_log = url_source.replace(tree, commits)

    git_commit = get_last_git_commit(
        src_dir=curdir,
        addon_path=addon_path if addon_path else None,
        is_addon=True if addon_path else False,
    )
    if git_commit["commit"] == "unknown":
        date_tag = "Accessed: {date}".format(date=git_commit["date"])
    else:
        date_tag = "Latest change: {date} in commit: {commit}".format(
            date=git_commit["date"], commit=git_commit["commit"]
        )
    sys.stdout.write(
        sourcecode.substitute(
            URL_SOURCE=url_source,
            PGM=pgm,
            URL_LOG=url_log,
            DATE_TAG=date_tag,
        )
    )
    sys.stdout.write(
        footer_index.substitute(
            INDEXNAME=index_name,
            INDEXNAMECAP=index_name_cap,
            YEAR=year,
            GRASS_VERSION=grass_version,
            HTML_PAGE_FOOTER_PAGES_PATH=html_page_footer_pages_path,
        ),
    )
else:
    sys.stdout.write(
        footer_noindex.substitute(
            YEAR=year,
            GRASS_VERSION=grass_version,
            HTML_PAGE_FOOTER_PAGES_PATH=html_page_footer_pages_path,
        ),
    )
