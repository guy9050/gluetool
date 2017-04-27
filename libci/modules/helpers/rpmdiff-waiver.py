import re
import os
from collections import defaultdict, namedtuple
import yaml
import requests
from requests_kerberos import HTTPKerberosAuth, OPTIONAL
from bs4 import BeautifulSoup
from libci import CIError, Module
from libci.utils import format_dict

RPMDIFF_RESULTS_TO_WAIVE = ["needs inspection", "failed"]
RPMDIFF_AUTOWAIVERS_QUERY = """SELECT
    rpmdiff_autowaive_rule.autowaive_rule_id as id,
    package_name,
    product_versions.name as product_version,
    rpmdiff_tests.description AS test,
    subpackage,
    content_pattern
FROM rpmdiff_autowaive_rule
JOIN rpmdiff_tests ON rpmdiff_autowaive_rule.test_id = rpmdiff_tests.test_id
JOIN rpmdiff_autowaive_product_versions ON
    rpmdiff_autowaive_rule.autowaive_rule_id = rpmdiff_autowaive_product_versions.autowaive_rule_id
JOIN product_versions ON rpmdiff_autowaive_product_versions.product_version_id = product_versions.id
WHERE active = 1 and package_name = %(package)s and product_versions.name IN %(products)s
ORDER BY 1, 2, 3, 4
"""
RPMDIFF_PRODUCT_VERSIONS_QUERY = """SELECT DISTINCT
    product_versions.name as product_version
FROM brew_tags
JOIN brew_tags_product_versions ON brew_tags.id = brew_tags_product_versions.brew_tag_id
JOIN product_versions ON brew_tags_product_versions.product_version_id = product_versions.id
WHERE brew_tags.name = %(brew_tag)s
"""

ERRATA_AUTOWAIVER_URL = "https://errata.devel.redhat.com/rpmdiff/show_autowaive_rule/{}"
RPMDIFF_WEBUI_COMMENT = "Autowaived with citool with these rules: {}"


class RpmDiffWaiverMatcher(object):
    # pylint: disable=too-few-public-methods

    def __init__(self, errors, waivers):
        self.errors = errors
        self.waivers = waivers
        self.matched = []

    def can_waive(self):
        for error in self.errors:
            if not self._waivers_match(error):
                return False
        return True

    def _waivers_match(self, error):
        for waiver in self.waivers:
            if error.subpackage != waiver.subpackage:
                continue
            if re.search(waiver.content_pattern, error.message):
                self.matched.append(waiver)
                return True
        return False


class RpmDiffWaiver(Module):
    """
    Helper module - give it a rpmdiff run Id, it will autowaive results,
    according to autowaivers in Errata tool.

    Below is an example of the yaml mapping file.
    .. code-block:: yaml
        ---
        # key is brew tag, values are product versions and can be string or list of strings
        rhel-7.4-candidate: RHEL-7
        rhel-7.1-z-candidate:
          - RHEL-7.1-EUS
          - RHEL-7.1.Z
    """

    name = 'rpmdiff-waiver'
    description = 'Run autowaivers from Errata on RPMDiff runs'
    mapping = None

    options = {
        'run-id': {
            'help': 'Task ID of RPMDiff run'
        },
        'package': {
            'help': 'Package name'
        },
        'target': {
            'help': 'Target'
        },
        'url': {
            'help': 'RPMdiff Hub URL'
        },
        'mapping': {
            'help': 'File with brew tag to product versions mapping'
        }
    }

    required_options = ['url']
    shared_functions = ['waive_results']

    def query_waivers(self, package, product_versions):
        cursor = self.shared("postgresql").cursor()
        search = {
            'package': package,
            'products': product_versions
        }
        cursor.execute(RPMDIFF_AUTOWAIVERS_QUERY, search)
        waivers = cursor.fetchall()
        categorized = defaultdict(list)
        for waiver in waivers:
            categorized[waiver.test].append(waiver)
        return categorized

    def query_product_versions(self, brew_tag):
        cursor = self.shared("postgresql").cursor()
        search = {
            'brew_tag': brew_tag
        }
        cursor.execute(RPMDIFF_PRODUCT_VERSIONS_QUERY, search)
        return tuple(row.product_version for row in cursor.fetchall())

    def parse_yaml(self):
        mapping = os.path.expanduser(self.option('mapping'))
        try:
            with open(mapping, 'r') as stream:
                self.mapping = yaml.load(stream)
                self.debug("module mapping config:\n{}".format(format_dict(self.mapping)))
        except yaml.YAMLError as e:
            raise CIError('unable to load mapping file: {}'.format(str(e)))

    def _map_tag_to_product(self, brew_tag):
        if self.mapping and brew_tag in self.mapping.keys():
            product_versions = self.mapping[brew_tag]
            if isinstance(product_versions, basestring):
                product_versions = (product_versions,)
            else:
                product_versions = tuple(product_versions)
            self.info("Manual mapping was successful, product versions: {}".format(product_versions))
            return product_versions
        else:
            self.warn("Manual mapping did not find product version")
        self.info("Search Errata DB for mapping")
        product_versions = self.query_product_versions(brew_tag)
        self.debug("Query results: {}".format(product_versions))
        if not product_versions:
            self.warn("Errata mapping did not find any product version")
            return None
        if len(product_versions) > 1:
            self.warn("Errata mapping is ambigous, more product versions found")
            # if we want continue only if mapping 1:1 exists
            # return None
        self.info("Errata mapping was successful, product versions: {}".format(product_versions))
        return product_versions

    @staticmethod
    def _download_errors(link):
        rows = BeautifulSoup(requests.get(link).text, "html.parser") \
            .find("table", attrs={"class": "result"}) \
            .find("table").find_all("tr")
        error = namedtuple('RpmDiffError', 'error_type subpackage message')
        errors = []
        for row in rows:
            columns = row.find_all("td")
            error_type = columns[0].find("b").getText().strip()
            if error_type.lower() in RPMDIFF_RESULTS_TO_WAIVE:
                errors.append(error(
                    error_type=error_type,
                    subpackage=columns[2].getText().strip(),
                    message=columns[4].find("pre").getText()
                ))
        return errors

    def waive_test(self, link, comment):
        # try kerberos login
        session = requests.session()
        url = self.option("url") + "/auth/login/?next=/"
        kerberos_auth = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
        if session.get(url, auth=kerberos_auth).status_code != 200:
            raise CIError("Authentication failed while waiving RPMDiff tests")
        # obtain token
        headers = {"Referer": link}
        token = BeautifulSoup(session.get(link, headers=headers).text, "html.parser") \
            .find("div", attrs={"id": "runDetail"})["data-token"]
        data = {
            "comment": comment,
            "action": "waive",
            "csrfmiddlewaretoken": token
        }
        # send post request to waive
        waive_request = session.post(link, data=data, headers=headers)
        return waive_request.status_code == 200

    def log_waivers(self, waivers):
        self.info("Found waivers: {}".format(sum(len(waiver) for waiver in waivers.itervalues())))
        for test_waivers in waivers.itervalues():
            for waiver in test_waivers:
                self.debug("{}: {}".format(waiver.test, waiver))

    def waive_results(self, run_id, package, target):
        """
        Waive results.

        :param str run_id: run id of rpmdiff run.
        """
        self.info("run-id: {}, package: {}, target: {}".format(run_id, package, target))
        if not run_id:
            raise CIError("you want waive results from RPMDiff but you did not specify run-id")
        if not self.has_shared('postgresql'):
            raise CIError("Module requires PostgreSQL support, did you include 'postgresql' module?")

        if self.option('mapping'):
            self.parse_yaml()
        else:
            self.warn("Mapping file not provided, manual mapping wont produce results")

        self.info("Map brew tag '{}' to product version".format(target))
        errata_products = self._map_tag_to_product(target)
        if not errata_products:
            raise CIError('No Errata product found for target: {}'.format(target))

        hub_url = self.option('url')

        self.info("Query waivers for product version: {}".format(errata_products))
        waivers = self.query_waivers(package, errata_products)
        if not waivers:
            self.info('No waivers found')
            return
        self.log_waivers(waivers)

        self.info("Download results from: {}".format(hub_url + "/run/{}".format(run_id)))
        results_page = requests.get(hub_url + "/run/{}".format(run_id)).text
        table = BeautifulSoup(results_page, "html.parser").find("table", attrs={"class": "summary"})
        if not table:
            raise CIError('Table of results was not found on RPMDiff WebUI')

        self.info("Looking into RPMDiff results for possible errors")
        for test_link in table.find_all("a"):
            test_name = test_link.getText()
            self.info("Looking into test '{}'".format(test_name))
            if test_name not in waivers.keys():
                self.info('No waivers for this test, skipping')
                continue
            self.info('Waivers for this test: {}'.format(len(waivers[test_name])))
            link = hub_url + test_link["href"]
            self.info('Download result table from: {}'.format(link))
            errors = self._download_errors(link)
            if not errors:
                self.info("There were no errors")
                continue
            matcher = RpmDiffWaiverMatcher(errors, waivers[test_name])
            if not matcher.can_waive():
                self.info("No all errors can be waived, skipping")
                continue
            log_msg = ""
            for waiver in matcher.matched:
                log_msg = "\n".join([log_msg, ERRATA_AUTOWAIVER_URL.format(waiver.id)])
            self.info("This test will be waived with there rules: {}".format(log_msg))

            if not self.waive_test(link, RPMDIFF_WEBUI_COMMENT.format(log_msg)):
                self.info("Test was probably not waived due to error in http")

        self.info("Waiving is complete")

    def rpmdiff_id_from_results(self):
        if not self.has_shared("results"):
            self.warn('Cannot obtain run-id, no \'results\' shared function found')
            return None
        results = self.shared("results")
        for result in results:
            if result.test_type == 'rpmdiff':
                return result.ids['rpmdiff_run_id']
        self.warn('Cannot obtain run-id, previous results do not contain rpmdiff result')
        return None

    def execute(self):
        run_id = self.option("run-id")
        package = self.option("package")
        target = self.option("target")
        if self.has_shared("brew_task"):
            brew_task = self.shared("brew_task")
            if not package:
                package = brew_task.component
            if not target:
                target = brew_task.target.destination_tag
        if not run_id:
            run_id = self.rpmdiff_id_from_results()

        self.waive_results(run_id, package, target)
