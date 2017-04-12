import re
import requests
from requests_kerberos import HTTPKerberosAuth, OPTIONAL
from bs4 import BeautifulSoup
import libci

RPMDIFF_RESULTS_TO_WAIVE = ["needs inspection", "failed"]
RPMDIFF_AUTOWAIVERS_QUERY = """SELECT
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
WHERE active = 1 and package_name = %(package)s and product_versions.name LIKE %(product)s
ORDER BY 1, 2, 3, 4
"""
# for information purpose about how look like errata product version in database
# RHEL-6 RHEL-6.8.z RHEL-6-SATELLITE-6.2 RHEL-7 RHEL-7.2.Z RHEL-7-SATELLITE-7.2
RPMDIFF_PRODUCT_VERSION_MAPPING = {
    'satellite-6.2.0-rhel-6-candidate': 'RHEL-7-SATELLITE-6.2',
}


class RpmDiffWaiver(libci.Module):
    """
    Helper module - give it a rpmdiff run Id, it will autowaive results,
    according to autowaivers in Errata tool.
    """

    name = 'rpmdiffwaiver'
    description = 'Run autowaivers from Errata on RPMDiff runs'

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
        }
    }

    required_options = ['url']
    shared_functions = ['waive_results']

    def query_waivers(self, package, product_version):
        cursor = self.shared("postgresql").cursor()
        search = {
            'package': package,
            'product': product_version
        }
        cursor.execute(RPMDIFF_AUTOWAIVERS_QUERY, search)
        waivers = cursor.fetchall()
        categorized = {}
        for waiver in waivers:
            if waiver.test in categorized:
                categorized[waiver.test].append(waiver)
            else:
                categorized[waiver.test] = [waiver]
        return categorized

    @staticmethod
    def _download_errors(link):
        rows = BeautifulSoup(requests.get(link).text, "html.parser") \
            .find("table", attrs={"class": "result"}) \
            .find("table").find_all("tr")
        errors = []
        for row in rows:
            columns = row.find_all("td")
            error_type = columns[0].find("b").getText().strip()
            error_detail1 = columns[2].getText().strip()
            error_detail2 = columns[4].find("pre").getText()
            if error_type.lower() in RPMDIFF_RESULTS_TO_WAIVE:
                errors.append((error_type, error_detail1, error_detail2))
        return errors

    @staticmethod
    def _waivers_match(error, waivers):
        for waiver in waivers:
            if error[1] != waiver.subpackage:
                continue
            if re.search(waiver.content_pattern, error[2]):
                return True
        return False

    def can_waive(self, errors, waivers):
        for error in errors:
            if not self._waivers_match(error, waivers):
                return False
        return True

    def waive_test(self, link):
        # try kerberos login
        session = requests.session()
        url = self.option("url") + "/auth/login/?next=/"
        kerberos_auth = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
        if session.get(url, auth=kerberos_auth).status_code != 200:
            raise Exception("Authentication failed while waiving RPMDiff tests")
        # obtain token
        headers = {"Referer": link}
        token = BeautifulSoup(session.get(link, headers=headers).text, "html.parser") \
            .find("div", attrs={"id": "runDetail"})["data-token"]
        data = {
            "comment": "Autowaived by citool",
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
            raise libci.CIError(
                "you want waive results from RPMDiff but you did not specify run-id")
        if not self.has_shared('postgresql'):
            raise libci.CIError(
                "Module requires PostgreSQL support, did you include 'postgresql' module?")
        if target not in RPMDIFF_PRODUCT_VERSION_MAPPING.keys():
            self.info('No Errata product found for target: {}'.format(target))
            return
        errata_product = RPMDIFF_PRODUCT_VERSION_MAPPING[target]
        hub_url = self.option('url')

        results_page = requests.get(hub_url + "/run/{}".format(run_id)).text
        table = BeautifulSoup(results_page, "html.parser").find("table", attrs={"class": "summary"})
        if not table:
            self.info('No table found')
            return

        self.info("Query waivers for product version: {}".format(errata_product))
        waivers = self.query_waivers(package, errata_product)
        if not waivers:
            self.info('No waivers found')
            return
        self.log_waivers(waivers)

        for test_link in table.find_all("a"):
            test_name = test_link.getText()
            self.info('Check test: {}'.format(test_name))
            if test_name not in waivers.keys():
                self.info('No waivers for this test')
                continue
            self.info('Waivers for this test: {}'.format(len(waivers[test_name])))
            link = hub_url + test_link["href"]
            self.info('Download result table')
            errors = self._download_errors(link)
            if not errors:
                self.info("There were not errors")
                continue
            if not self.can_waive(errors, waivers[test_name]):
                self.info("No all errors can be waived, skipping")
                continue
            self.info("This test will be waived")
            if not self.waive_test(link):
                self.info("Test was probably not waived due to error in http")

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
