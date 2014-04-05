from __future__ import print_function
"""
:copyright: 2010 by Ronny Pfannschmidt
:license: MIT

"""

# Genscript Metadata

import re
import os
import sys
import shlex
import subprocess
import datetime


def trace_debug(*k):
    print(*k)
    sys.stdout.flush()


def trace(*k):
    pass


def do_ex(cmd, cwd='.'):
    trace('cmd', repr(cmd))
    p = subprocess.Popen(
        shlex.split(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=dict(
            os.environ,
            #disable hgrc processing other than .hg/hgrc
            HGRCPATH='',
            # try to disable i18n
            LC_ALL='C',
            LANGUAGE='',
            HGPLAIN='1',
        )
    )

    out, err = p.communicate()
    if out:
        trace('out', repr(out))
    if err:
        trace('err', repr(err))
    if p.returncode:
        trace('ret', p.returncode)
    return out.strip().decode(), err.strip().decode(), p.returncode


def do(cmd, cwd='.'):
    out, err, ret = do_ex(cmd, cwd)
    if ret:
        trace('ret', ret)
        print(err)
    return out

# extended pep 386 regex
# see http://www.python.org/dev/peps/pep-0386/#the-new-versioning-algorithm
version_re = r"""^
(?P<prefix>\w+-?)?         # any text, may have a dash
                              # custom to deal with tag prefixes
(?P<version>\d+\.\d+)         # minimum 'N.N'
(?P<extraversion>(?:\.\d+)*)  # any number of extra '.N' segments
(?P<prerelfullversion>
(:?
    (?P<prerel>[abc]|rc)         # 'a' = alpha, 'b' = beta
                                 # 'c' or 'rc' = release candidate
    (?P<prerelversion>\d+(?:\.\d+)*)
)?)
# we dont mach those, its our job to generate them
##(?P<postdev>(\.post(?P<post>\d+))?(\.dev(?P<dev>\d+))?)?
$"""


def tag_to_version(tag):
    match = re.match(version_re, tag, re.VERBOSE)
    if match is not None:
        return ''.join(match.group(
            'version', 'extraversion', 'prerelfullversion',
        ))


def tags_to_versions(tags):
    versions = map(tag_to_version, tags)
    return list(filter(None, versions))


def _version(tag, distance=0, node=None, dirty=False):
    tag = tag_to_version(tag)
    time = datetime.date.today().strftime('%Y%m%d')
    return dict(
        tag=tag,
        distance=distance,
        node=node,
        dirty=dirty,
        time=time,
    )


def version_from_cachefile(root, cachefile=None):
    #XXX: for now we ignore root
    if not cachefile or not os.path.exists(cachefile):
        return
    #replaces 'with open()' from py2.6
    fd = open(cachefile)
    fd.readline()  # remove the comment
    version = None
    try:
        line = fd.readline()
        version_string = line.split(' = ')[1].strip()
        version = version_string[1:-1]
    except:  # any error means invalid cachefile
        pass
    fd.close()
    return version


def _hg_tagdist_normalize_tagcommit(root, tag, dist, node):
    dirty = node.endswith('+')
    node = node.strip('+')
    st = do('hg st --no-status --change %s' % str(node), root)

    trace('normalize', locals())
    if int(dist) == 1 and st == '.hgtags' and not dirty:
        return _version(tag)
    else:
        return _version(tag, distance=dist, node=node, dirty=dirty)


def version_from_hg(root, cachefile=None):
    # no .hg means no way to get it
    if not os.path.isdir(os.path.join(root, '.hg')):
        return
    l = do('hg id -i -t', root).split()
    node = l.pop(0)
    tags = tags_to_versions(l)
    if tags:
        return _version(tags[0], dirty=node[-1] == '+')  # '' or '+'

    if node.strip('+') == '0'*12:
        trace('initial node', root)
        return _version('0.0', dirty=node[-1] == '+')

    cmd = 'hg parents --template "{node} {latesttag} {latesttagdistance}"'
    out = do(cmd, root)
    try:
        node, tag, dist = out.split()
        if tag == 'null':
            tag = '0.0'
            dist = 1
        return _hg_tagdist_normalize_tagcommit(root, tag, dist, node)
    except ValueError:
        pass  # unpacking failed, old hg


def version_from_git(root, cachefile=None):
    if not os.path.exists(os.path.join(root, '.git')):
        return
    rev_node, _, ret = do_ex('git rev-parse --verify --quiet HEAD', root)
    if ret:
        return _version('0.0')

    out, err, ret = do_ex('git describe --dirty --tags', root)
    if '-' not in out and '.' not in out:
        revs = do('git rev-list HEAD', root)
        count = revs.count('\n')
        if ret:
            out = rev_node[:7]
        return _version('0.0', distance=count + 1, node=out)
    if ret:
        return
    dirty = out.endswith('-dirty')
    if dirty:
        out = out.rsplit('-', 1)[0]
    if '-' not in out:
        return _version(out, node=rev_node[:7], dirty=dirty)
    else:
        tag, number, node = out.split('-')
        return _version(tag, distance=number, node=node, dirty=dirty)


def _archival_to_version(data):
    """stolen logic from mercurials setup.py"""
    if 'tag' in data:
        return _version(data['tag'])
    elif 'latesttag' in data:
        return _version(data['latesttag'],
                        distance=data['latesttagdistance'],
                        node=data['node'][:12])
    else:
        return _version('0.0', node=data.get('node', '')[:12])


def _data_from_archival(path):
    with open(path) as fp:
        content = fp.read()
    trace('content', repr(content))
    # the complex conditions come from reading pseudo-mime-messages
    data = dict(
        x.split(': ', 1)
        for x in content.splitlines()
        if ': ' in x)
    trace('data', data)
    return data

def version_from_archival(root, cachefile=None):
    for parent in root, os.path.dirname(root):
        archival = os.path.join(parent, '.hg_archival.txt')
        if os.path.exists(archival):
            data = _data_from_archival(archival)
            return _archival_to_version(data)


def version_from_sdist_pkginfo(root, cachefile=None):
    pkginfo = os.path.join(root, 'PKG-INFO')
    if os.path.exists(pkginfo):
        trace('pkginfo', pkginfo)
        data = _data_from_archival(pkginfo)
        version = data.get('Version')
        if version != 'UNKNOWN':
            return version


def write_cachefile(path, version):
    fd = open(path, 'w')
    try:
        fd.write('# this file is autogenerated by hgdistver + setup.py\n')
        fd.write('version = "%s"\n' % version)
    finally:
        fd.close()


methods = [
    version_from_hg,
    version_from_git,
    version_from_archival,
    version_from_cachefile,
    version_from_sdist_pkginfo,
]


def format_version(version):
    if not isinstance(version, dict):
        trace('string')
        return version

    if version['dirty']:
        import time
        version['time'] = time.strftime('%Y%m%d')
        return "%(tag)s.post%(distance)s-%(node)s+%(time)s" % version
    elif version['distance']:
        return "%(tag)s.post%(distance)s-%(node)s" % version
    else:
        return str(version['tag'])


def _extract_version(root, cachefile):
    version = None
    for method in methods:
        version = method(root=root, cachefile=cachefile)
        if version:
            trace('method', method.__name__, version)
            return format_version(version)


def get_version(cachefile=None, root=None):
    if root is None:
        root = os.getcwd()
    trace('root', repr(root))
    if cachefile is not None:
        cachefile = os.path.join(root, cachefile)
    trace('cachefile', repr(cachefile))

    version = _extract_version(root, cachefile)

    if cachefile and version:
        write_cachefile(cachefile, version)
    return version


def setuptools_version_keyword(dist, keyword, value):
    if value:
        dist.metadata.version = get_version(
            cachefile=getattr(dist, 'cache_hg_version_to', None))


def setuptools_cachefile_keyword(dist, keyword, value):
    pass


def find_hg_files(dirname=''):
    return do('hg st -armdc --no-status', dirname or '.').splitlines()


def find_git_files(dirname=''):
    return do('git ls-files', dirname or '.').splitlines()


def findroot(path, req):
    old = None
    while path != old:
        if os.path.exists(os.path.join(path, req)):
            return path
        old = path
        path = os.path.dirname(path)


def find_files(dirname=''):
    abs = os.path.abspath(dirname)
    hg = findroot(abs, '.hg')
    git = findroot(abs, '.git')
    if hg and git:
        if hg >= git:  # prefer hg in case of both, could be hg-git
            git = None
    if hg:
        return find_hg_files(dirname)
    elif git:
        return find_git_files(dirname)


if __name__ == '__main__':
    print('Guessed Version', get_version())
    if 'ls' in sys.argv:
        for fname in find_files('.'):
            print(fname)
