"""
Deploy this project in dev/stage/production.

Requires commander_ which is installed on the systems that need it.

.. _commander: https://github.com/oremj/commander
"""

import os
from os.path import join as pjoin
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fabric.api import (env, execute, lcd, local, parallel,
                        run, roles, task)

from fabdeploytools.rpm import RPMBuild
from fabdeploytools import helpers
import fabdeploytools.envs

import deploysettings as settings

env.key_filename = settings.SSH_KEY
fabdeploytools.envs.loadenv(pjoin('/etc/deploytools/envs',
                                  settings.CLUSTER))

SOLITUDE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(SOLITUDE)
VIRTUALENV = pjoin(ROOT, 'venv')
PYTHON = pjoin(VIRTUALENV, 'bin', 'python')


@task
def create_virtualenv():
    venv = VIRTUALENV
    if venv.startswith(pjoin('/data', 'src', settings.CLUSTER)):
        local('rm -rf %s' % venv)

    helpers.create_venv(VIRTUALENV, settings.PYREPO,
                        pjoin(SOLITUDE, 'requirements/prod.txt'))


@task
def update_assets():
    with lcd(SOLITUDE):
        local("%s manage.py collectstatic --noinput" % PYTHON)
        # LANG=en_US.UTF-8 is sometimes necessary for the YUICompressor.
        local('LANG=en_US.UTF8 %s manage.py compress_assets' % PYTHON)


@task
def update_db():
    """Update the database schema, if necessary.

    Uses schematic by default. Change to south if you need to.

    """
    if not getattr(settings, 'IS_PROXY', False):
        with lcd(SOLITUDE):
            local("%s %s/bin/schematic migrations" %
                  (PYTHON, VIRTUALENV))


@task
@roles('celery')
@parallel
def update_celery(ctx):
    if getattr(settings, 'CELERY_SERVICE', False):
        run("/sbin/service %s restart" % settings.CELERY_SERVICE)


@task
def update_info():
    """Write info about the current state to a publicly visible file."""
    with lcd(SOLITUDE):
        local('date')
        local('git branch')
        local('git log -3')
        local('git status')
        local('git submodule status')


@task
def disable_cron():
    local("rm -f /etc/cron.d/%s" % settings.CRON_NAME)


@task
def install_cron():
    with lcd(SOLITUDE):
        local('%s ./bin/update/gen-crons.py '
              '-p %s -u apache -w %s > /etc/cron.d/.%s' %
              (PYTHON, PYTHON, SOLITUDE,
               settings.CRON_NAME))

        local('mv /etc/cron.d/.%s /etc/cron.d/%s' % (settings.CRON_NAME,
                                                     settings.CRON_NAME))


@task
def pre_update(ref):
    """Update code to pick up changes to this file."""
    execute(disable_cron)
    execute(helpers.git_update, SOLITUDE, ref)
    execute(update_info)


@task
def update():
    create_virtualenv()
    update_db()


@task
@roles('web', 'celery')
@parallel
def install_package(rpmbuild):
    rpmbuild.install_package()


@task
@roles('web')
@parallel
def restart_workers():
    run("/sbin/service %s restart" % settings.GUNICORN)


@task
def deploy():
    with lcd(SOLITUDE):
        ref = local('git rev-parse HEAD', capture=True)

    rpmbuild = RPMBuild(name='zamboni',
                        env=settings.ENV,
                        ref=ref,
                        cluster=settings.CLUSTER,
                        domain=settings.DOMAIN)

    rpmbuild.build_rpm(ROOT)
    execute(install_package, rpmbuild)
    execute(restart_workers)
    execute(update_celery)
    rpmbuild.clean()

    execute(install_cron)
    with lcd(SOLITUDE):
        local('%s manage.py statsd_ping --key=update' % PYTHON)