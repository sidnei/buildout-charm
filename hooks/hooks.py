#!/usr/bin/python

import os
import subprocess
import sys
import contextlib
import io
import hashlib

from shutil import copy2 as copy
from os import (
    makedirs,
    chown,
    getcwd,
    chdir,
    )
from os.path import (
    join,
    exists,
    )

from pwd import getpwnam
from grp import getgrnam

from charmhelpers.core.hookenv import (
    Hooks,
    config as config_get,
    log,
    )

hooks = Hooks()

service_uid = 1500
service_gid = 1500


@contextlib.contextmanager
def cd(new_path):
    original_path = getcwd()
    chdir(new_path)
    yield
    chdir(original_path)


def run(command, *args, **kwargs):
    log("Executing %s." % " ".join(command))
    kwargs.setdefault("stdout", sys.stdout)
    kwargs.setdefault("stderr", sys.stderr)
    subprocess.check_call(command, *args, **kwargs)


def copy_if_needed(src, dest):
    if not exists(dest) or checksum(src) != checksum(dest):
        copy(src, dest)
        return True
    return False


def is_basenode():
    if os.path.exists('/etc/rsyncd.d'):
        return True
    else:
        return False


def comma_split(value):
    values = value.split(",")
    return filter(None, (v.strip() for v in values))


def checksum(filename):
    hasher = hashlib.sha256()
    with io.open(filename, 'r', buffering=io.DEFAULT_BUFFER_SIZE) as f:
        while True:
            chunk = f.read(io.DEFAULT_BUFFER_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
        return hasher.hexdigest()


def install_file(dest, content=None, callback=None,
                 owner="root", group="root", mode=0600):
    original_checksum = None
    if exists(dest):
        original_checksum = checksum(dest)
    uid = getpwnam(owner)[2]
    gid = getgrnam(group)[2]
    dest_fd = os.open(dest, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, mode)
    os.fchown(dest_fd, uid, gid)
    with os.fdopen(dest_fd, "w") as destfile:
        if content is not None:
            destfile.write(content)
        else:
            callback(destfile)
    return checksum(dest) != original_checksum


def apt_get_install(packages=None):
    if packages is None:
        return False
    cmd_line = ["/usr/bin/apt-get", "-yy", "--force-yes",
                "-o DPkg::Options::=--force-confold", "install", "-qq"]
    try:
        cmd_line.extend(packages.split())
    except AttributeError:
        cmd_line.extend(packages)
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return run(cmd_line, env=env)


def create_service_group(service_group):
    try:
        getgrnam(service_group)
    except KeyError:
        run(["/usr/sbin/groupadd", "-fr", "--gid",
             str(service_gid), service_group])


def create_service_user(service_user):
    try:
        getpwnam(service_user)
    except KeyError:
        run(["/usr/sbin/adduser", "--disabled-password", "--system",
             "--uid", str(service_uid),
             "--gid", str(service_gid), service_user])


@hooks.hook("config-changed")
def config_changed():
    config = config_get()
    if config["packages"].strip():
        apt_get_install(set(["python-virtualenv", "python-dev"] +
                            comma_split(config["packages"])))

    create_service_group(config["service-group"])
    create_service_user(config["service-user"])

    if not exists(config["buildout-dir"]):
        makedirs(config["buildout-dir"])
    chown(config["buildout-dir"],
          service_uid,
          service_gid)

    buildout_cfg = join(config["buildout-dir"], "buildout.cfg")
    if config["buildout"].strip():
        install_file(buildout_cfg,
                     config["buildout"],
                     owner=config["service-user"],
                     group=config["service-group"])

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "true"

    with cd(config["buildout-dir"]):
        if not exists(join(config["buildout-dir"], "venv")):
            run(["sudo", "-u", config["service-user"], "-n", "--",
                 "virtualenv", "--no-site-packages", "venv"], env=env)
        buildout = "zc.buildout"
        if config["buildout-version"]:
            buildout += "==" + config["buildout-version"]
        run(["sudo", "-u", config["service-user"], "-n", "--",
             "./venv/bin/easy_install", buildout], env=env)
        run(["sudo", "-u", config["service-user"], "-n", "--",
             "./venv/bin/buildout", "-vv"], env=env)


if __name__ == "__main__":
    hooks.execute(sys.argv)
