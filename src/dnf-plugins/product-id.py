from __future__ import print_function, division, absolute_import

#
# Copyright (c) 2015 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.
#

import logging

from subscription_manager import logutil
from subscription_manager.productid import ProductManager
from subscription_manager.utils import chroot
from subscription_manager.injectioninit import init_dep_injection

from dnfpluginscore import _, logger
import dnf
import dnf.base
import librepo


class ProductId(dnf.Plugin):
    name = 'product-id'

    def __init__(self, base, cli):
        super(ProductId, self).__init__(base, cli)
        self.base = base
        self.cli = cli

    def transaction(self):
        """
        Update product ID certificates.
        """
        if len(self.base.transaction) == 0:
            # nothing to update after empty transaction
            return

        try:
            init_dep_injection()
        except ImportError as e:
            logger.error(str(e))
            return

        logutil.init_logger_for_yum()
        chroot(self.base.conf.installroot)
        try:
            # We create own base object to get fresh list of installed packages
            # and to get list of available packages after installing/removing,
            # because base object from dnf.Plugin provides incomplete and obsolete data.
            with dnf.base.Base() as base:
                base.read_all_repos()
                base.fill_sack(load_system_repo=True, load_available_repos=True)
                pm = DnfProductManager(base)
                pm.update_all()
            logger.info(_('Installed products updated.'))
        except Exception as e:
            logger.error(str(e))


log = logging.getLogger('rhsm-app.' + __name__)


class DnfProductManager(ProductManager):
    def __init__(self, base):
        self.base = base
        ProductManager.__init__(self)

    def update_all(self):
        return self.update(self.get_enabled(),
                           self.get_active(),
                           True)

    def _download_productid(self, repo, tmpdir):
        handle = repo._handle_new_remote(tmpdir)
        handle.setopt(librepo.LRO_PROGRESSCB, None)
        handle.setopt(librepo.LRO_YUMDLIST, [self.PRODUCTID])
        res = handle.perform()
        return res.yum_repo.get(self.PRODUCTID, None)

    def get_enabled(self):
        """find repos that are enabled"""
        lst = []
        enabled = self.base.repos.iter_enabled()

        # skip repo's that we don't have productid info for...
        for repo in enabled:
            try:
                with dnf.util.tmpdir() as tmpdir:
                    fn = self._download_productid(repo, tmpdir)
                    if fn:
                        cert = self._get_cert(fn)
                        if cert is None:
                            continue
                        lst.append((cert, repo.id))
                    else:
                        # We have to look in all repos for productids, not just
                        # the ones we create, or anaconda doesn't install it.
                        self.meta_data_errors.append(repo.id)
            except Exception as e:
                log.warning("Error loading productid metadata for %s." % repo)
                log.exception(e)
                self.meta_data_errors.append(repo.id)

        if self.meta_data_errors:
            log.debug("Unable to load productid metadata for repos: %s",
                      self.meta_data_errors)
        return lst

    # find the list of repo's that provide packages that
    # are actually installed.
    def get_active(self):
        """find repos that have packages installed"""

        # Fill sack with fresh data to include newly installed packages
        self.base.fill_sack()

        # installed packages
        q_installed = self.base.sack.query().installed()
        if hasattr(q_installed, "_na_dict"):
            # dnf 2.0
            installed_na = q_installed._na_dict()
        else:
            # dnf 1.0
            installed_na = q_installed.na_dict()

        # available version of installed
        avail_pkgs = self.base.sack.query().available().filter(name=[
            k[0] for k in list(installed_na.keys())])

        active = set()
        for p in avail_pkgs:
            if (p.name, p.arch) in installed_na:
                active.add(p.repoid)

        return active
