# pylint: disable=protected-access
import pytest

from mock import MagicMock, PropertyMock
import libci
import libci.utils
import libci.modules.static_analysis.rpmdiff.rpmdiff
import libci.modules.testing.testing_results
from . import Bunch, create_module


SUCCESS_STDOUT = """{
  "rpmdiff_rungroup": null,
  "new_version": "2.21-2.el8_3",
  "run_date": "2017-07-20T07:57:04.808959Z",
  "package_name": "which",
  "run_id": 118618,
  "variant": "",
  "old_version": "",
  "obsolete": 0,
  "web_url": "https://rpmdiff.engineering.redhat.com/run/118618",
  "errata_nr": "yakko",
  "owner": {
    "url": "https://rpmdiff-hub.host.prod.eng.bos.redhat.com/api/v1/users/148/",
    "username": "jenkins+baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com",
    "is_staff": false,
    "email": "jenkins+baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com@redhat.com"
  },
  "overall_score": {
    "score": 0,
    "description": "Passed"
  }
}
"""
FAILED_STDOUT = """{
  "rpmdiff_rungroup": null,
  "new_version": "2.21-2.el8_3",
  "run_date": "2017-07-20T07:57:04.808959Z",
  "package_name": "which",
  "run_id": 118618,
  "variant": "",
  "old_version": "",
  "obsolete": 0,
  "web_url": "https://rpmdiff.engineering.redhat.com/run/118618",
  "errata_nr": "yakko",
  "owner": {
    "url": "https://rpmdiff-hub.host.prod.eng.bos.redhat.com/api/v1/users/148/",
    "username": "jenkins+baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com",
    "is_staff": false,
    "email": "jenkins+baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com@redhat.com"
  },
  "overall_score": {
    "score": 4,
    "description": "Failed"
  }
}
"""
RUNNING_STDOUT = """{
  "rpmdiff_rungroup": null,
  "new_version": "2.21-2.el8_3",
  "run_date": "2017-07-20T07:57:04.808959Z",
  "package_name": "which",
  "run_id": 118618,
  "variant": "",
  "old_version": "",
  "obsolete": 0,
  "web_url": "https://rpmdiff.engineering.redhat.com/run/118618",
  "errata_nr": "yakko",
  "owner": {
    "url": "https://rpmdiff-hub.host.prod.eng.bos.redhat.com/api/v1/users/148/",
    "username": "jenkins+baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com",
    "is_staff": false,
    "email": "jenkins+baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com@redhat.com"
  },
  "overall_score": {
    "score": 0,
    "description": "Running"
  }
}
"""
PASSED_RESULTS_STDOUT = """{
  "results": [
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 13,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-abi-symbols",
        "description": "ABI symbols.6",
        "long_desc": "This test shows symbols in libraries going away"
      },
      "result_id": 3542382,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 30,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-build-log",
        "description": "Build Log",
        "long_desc": "These tests check regressions in the build-time make check output"
      },
      "result_id": 3542383,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 22,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-changelog",
        "description": "RPM changelog",
        "long_desc": "These tests check changelogs"
      },
      "result_id": 3542384,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 21,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-configdoc",
        "description": "RPM config/doc files",
        "long_desc": "This test shows changes in %config-ness of files"
      },
      "result_id": 3542385,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 31,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-valid-file",
        "description": "Desktop file sanity",
        "long_desc": "This test validates .desktop files"
      },
      "result_id": 3542386,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 11,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-elf-binarylibrary",
        "description": "Elflint",
        "long_desc": "This test shows regressions in the output of the elflint tool"
      },
      "result_id": 3542387,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 43,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-empty-payload",
        "description": "Empty payload",
        "long_desc": "This test checks for packages with empty payloads"
      },
      "result_id": 3542388,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 7,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-elf-binarylibrary",
        "description": "Execshield",
        "long_desc": "This test shows changes in security flags in ELF files"
      },
      "result_id": 3542389,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 3,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-file-list",
        "description": "File list",
        "long_desc": "This test shows changes in the file manifest of rpms"
      },
      "result_id": 3542390,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 42,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-ipv6",
        "description": "IPv6",
        "long_desc": "This test checks for IPv6 issues"
      },
      "result_id": 3542391,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 49,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-java-byte-code",
        "description": "Java byte code",
        "long_desc": "This test checks byte code changes in java class files"
      },
      "result_id": 3542392,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 19,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-valid-file",
        "description": "Manpage integrity",
        "long_desc": "This test checks if manpages are valid"
      },
      "result_id": 3542393,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 47,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-metadata",
        "description": "Metadata",
        "long_desc": "This test checks the metadata stored in the package headers"
      },
      "result_id": 3542394,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 18,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-multilib",
        "description": "Multilib regressions",
        "long_desc": "This test shows changes in multilibness of files"
      },
      "result_id": 3542395,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 44,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-ownership",
        "description": "Ownership",
        "long_desc": "This test checks the ownership of files in the RPM payload"
      },
      "result_id": 3542396,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 17,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-patches",
        "description": "Patches",
        "long_desc": "This test shows any new patches that are not applied in the spec file"
      },
      "result_id": 3542397,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 48,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-pathnames",
        "description": "Pathnames",
        "long_desc": "This test checks for file paths that do not conform to RHEL guidelines"
      },
      "result_id": 3542398,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 6,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-file-permissions",
        "description": "File permissions",
        "long_desc": "This test shows changes in file permission"
      },
      "result_id": 3542399,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 35,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-politics",
        "description": "Politics",
        "long_desc": "This test checks for political issues"
      },
      "result_id": 3542400,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 20,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-rpm-requires-provides",
        "description": "RPM requires/provides",
        "long_desc": "This test shows changes in Provides: and Requires of rpms"
      },
      "result_id": 3542401,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 45,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-rpath",
        "description": "RPath",
        "long_desc": "This test checks the validity of RPATH directories used by binaries"
      },
      "result_id": 3542402,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 24,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-rpm-scripts-and-triggers",
        "description": "RPM scripts",
        "long_desc": "This test checks which scripts changed in the rpm"
      },
      "result_id": 3542403,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 36,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-shell-scripts",
        "description": "Shell Syntax",
        "long_desc": "This test checks the syntax of shell scripts"
      },
      "result_id": 3542404,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 5,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-file-sizes",
        "description": "File sizes",
        "long_desc": "This test shows significant changes in file size"
      },
      "result_id": 3542405,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 29,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-specfile",
        "description": "Specfile checks",
        "long_desc": "These tests check %post sections of specfiles"
      },
      "result_id": 3542406,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 4,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-elf-stripping",
        "description": "Binary stripping",
        "long_desc": "This test shows changes in stripping of ELF files"
      },
      "result_id": 3542407,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 27,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-symlinks",
        "description": "Symlinks",
        "long_desc": "This test shows symlinks that became dangling"
      },
      "result_id": 3542408,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 23,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-rpm-scripts-and-triggers",
        "description": "RPM triggers",
        "long_desc": "This tests shows if triggers changed in the rpm"
      },
      "result_id": 3542409,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 28,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-upstream",
        "description": "Upstream Source",
        "long_desc": "This test verifies that the upstream source tarballs did not change"
      },
      "result_id": 3542410,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 25,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-virus-scan",
        "description": "Virus scan",
        "long_desc": "This test shows the results of the virus scan"
      },
      "result_id": 3542411,
      "can_approve_waiver": "",
      "need_push_priv": 0
    },
    {
      "score_old": null,
      "run": 118618,
      "log": "<table>\\n</table>\\n",
      "score": 0,
      "test": {
        "test_id": 26,
        "wiki_url": "https://docs.engineering.redhat.com/display/HTD/rpmdiff-valid-file",
        "description": "XML validity",
        "long_desc": "This test shows XML files that have gone bad"
      },
      "result_id": 3542412,
      "can_approve_waiver": "",
      "need_push_priv": 0
    }
  ]
}
"""


@pytest.fixture(name='module')
def fixture_module():
    return create_module(libci.modules.static_analysis.rpmdiff.rpmdiff.CIRpmdiff)


@pytest.fixture(name="brew_task")
def fixture_brew_task():
    mocked_task = MagicMock()
    mocked_scratch = PropertyMock(return_value=False)
    mocked_nvr = PropertyMock(return_value="1.3")
    mocked_task_id = PropertyMock(return_value=1)
    mocked_latest = PropertyMock(return_value="0.9")
    mocked_component = PropertyMock(return_value="which")

    def tostring(self):
        # pylint: disable=unused-argument
        return "1"
    mocked_task_id.__str__ = tostring
    type(mocked_task).scratch = mocked_scratch
    type(mocked_task).nvr = mocked_nvr
    type(mocked_task).task_id = mocked_task_id
    type(mocked_task).latest = mocked_latest
    type(mocked_task).component = mocked_component
    return mocked_task


@pytest.fixture(name="scratch_brew_task")
def fixture_scratch_brew_task(brew_task):
    type(brew_task).scratch = PropertyMock(return_value=True)
    return brew_task


@pytest.fixture(name="module_with_task")
def fixture_module_with_task(module, brew_task):
    ci, module = module
    module.brew_task = brew_task
    return ci, module


@pytest.fixture(name="module_with_ciresults")
def fixture_module_with_ciresults(module_with_task):
    ci, module = module_with_task
    module_results = libci.modules.testing.testing_results.TestingResults(ci, "dummy_results_module")
    module_results.add_shared()
    return ci, module


@pytest.fixture(name="configured_module")
def fixture_configured_module(module):
    ci, module = module
    module._config.update({
        "blacklist": None,
        "run-id": None,
        "type": "analysis",
        "url": None
    })
    return ci, module


@pytest.fixture(name="module_for_execute")
def fixture_module_execute(configured_module, monkeypatch, brew_task):
    ci, module = configured_module
    run_rpmdiff_mock = MagicMock()
    runinfo_mock = MagicMock()
    publish_mock = MagicMock()
    monkeypatch.setattr(module, "_run_rpmdiff", run_rpmdiff_mock)
    monkeypatch.setattr(module, "_get_runinfo", runinfo_mock)
    monkeypatch.setattr(module, "_publish_results", publish_mock)

    def shared_mock(key):
        return {
            'brew_task': brew_task,
            'results': ci.shared(key)
        }[key]
    monkeypatch.setattr(module, "shared", shared_mock)
    return ci, module


def passed_run_command(cmd, **kwargs):
    # pylint: disable=unused-argument
    if cmd and "results" in cmd[-1]:
        return Bunch(exit_code=0, stdout=PASSED_RESULTS_STDOUT, stderr="")
    return Bunch(exit_code=0, stdout=SUCCESS_STDOUT, stderr="")


def faulty_run_command(cmd, **kwargs):
    # pylint: disable=unused-argument
    raise libci.CICommandError(cmd, Bunch(exit_code=1, stderr="error msg"))


def failed_run_command(cmd, **kwargs):
    # pylint: disable=unused-argument
    if cmd and "results" in cmd[-1]:
        return Bunch(exit_code=0, stdout=PASSED_RESULTS_STDOUT, stderr="")
    return Bunch(exit_code=0, stdout=FAILED_STDOUT, stderr="")


def test_shared(module):
    ci, _ = module
    assert ci.has_shared('refresh_rpmdiff_results') is True


def test_loadable(module):
    ci, _ = module
    python_mod = ci._load_python_module("static_analysis/rpmdiff", "pytest_rpmdiff",
                                        "libci/modules/static_analysis/rpmdiff/rpmdiff.py")
    assert hasattr(python_mod, "CIRpmdiff")


def test_run_command(module, monkeypatch):
    _, module = module
    run_mock = MagicMock()
    monkeypatch.setattr(libci.utils, "run_command", run_mock)
    module._run_command(["arg0", "arg1", "arg2"])
    run_mock.assert_called_with(["arg0", "arg1", "arg2"])


def test_run_command_fail(module, monkeypatch):
    _, module = module
    monkeypatch.setattr(libci.utils, "run_command", faulty_run_command)
    with pytest.raises(libci.CIError, match=r"^Failure during 'rpmdiff-remote' execution:"):
        module._run_command(["arg0", "arg1", "arg2"])


def test_create_command(module):
    _, module = module
    assert module._rpmdiff_cmd == ["rpmdiff-remote"]


def test_create_command_with_huburl(module):
    _, module = module
    module.hub_url = "url"
    assert module._rpmdiff_cmd == ["rpmdiff-remote", "--hub-url", "url"]


def test_get_runinfo(module, monkeypatch):
    _, module = module
    run_mock = MagicMock(return_value=MagicMock(exit_code=0, stdout=SUCCESS_STDOUT, stderr=""))
    monkeypatch.setattr(libci.utils, "run_command", run_mock)
    blob = module._get_runinfo(118618)
    run_mock.assert_called_with(["rpmdiff-remote", "runinfo", "118618"])
    assert blob["run_id"] == 118618
    assert blob["package_name"] == "which"
    assert blob["overall_score"]["score"] == 0
    assert blob["overall_score"]["description"] == "Passed"


def test_wait_until_finished_finished(module, monkeypatch):
    _, module = module
    run_mock = MagicMock(return_value=MagicMock(exit_code=0, stdout=SUCCESS_STDOUT, stderr=""))
    monkeypatch.setattr(libci.utils, "run_command", run_mock)
    blob = module._wait_until_finished(118618)
    assert blob["run_id"] == 118618


def test_wait_until_finished_timeout_exceed(module, monkeypatch):
    _, module = module
    module.max_timeout = 2
    module.check_interval = 1
    run_mock = MagicMock(return_value=MagicMock(exit_code=0, stdout=RUNNING_STDOUT, stderr=""))
    monkeypatch.setattr(libci.utils, "run_command", run_mock)
    with pytest.raises(libci.CIError, match=r"^Waiting for RPMdiff results timed out"):
        module._wait_until_finished(118618)


@pytest.mark.parametrize("scratch,rpmdiff_test_type,expected_schedule_cmd", [
    (False, "comparison", ["rpmdiff-remote", "schedule", "1.3", "--baseline", "0.9"]),
    (True, "comparison", ["rpmdiff-remote", "schedule", "1", "--baseline", "0.9"]),
    (False, "analysis", ["rpmdiff-remote", "schedule", "1.3"]),
    (True, "analysis", ["rpmdiff-remote", "schedule", "1"]),
])
def test_run_rpmdiff(module_with_task, monkeypatch, scratch, rpmdiff_test_type, expected_schedule_cmd):
    _, module = module_with_task
    type(module.brew_task).scratch = PropertyMock(return_value=scratch)
    run_mock = MagicMock(return_value=MagicMock(exit_code=0, stdout=SUCCESS_STDOUT, stderr=""))
    monkeypatch.setattr(libci.utils, "run_command", run_mock)
    blob = module._run_rpmdiff(rpmdiff_test_type, "0.9")
    run_mock.assert_any_call(expected_schedule_cmd)
    assert blob["run_id"] == 118618


def test_run_rpmdiff_comparison_nobaseline(module_with_task):
    _, module = module_with_task
    with pytest.raises(libci.CIError, match=r"^Not provided baseline for comparison"):
        module._run_rpmdiff("comparison", nvr_baseline=None)


@pytest.mark.parametrize(
    "rpmdiff_test_type,expected_results", [
        ("comparison", [
            "rpmdiff-comparison", "koji_build_pair", "1.3 0.9",
            "dist.rpmdiff.comparison", "dist.rpmdiff.comparison.abi_symbols_6"
        ]),
        ("analysis", [
            "rpmdiff-analysis", "koji_build", "1.3",
            "dist.rpmdiff.analysis", "dist.rpmdiff.analysis.abi_symbols_6"
        ])
    ])
def test_publish_results(module_with_ciresults, monkeypatch, rpmdiff_test_type, expected_results):
    ci, module = module_with_ciresults
    monkeypatch.setattr(libci.utils, "run_command", passed_run_command)
    assert not ci.shared("results")
    module._publish_results(module._get_runinfo(118618), rpmdiff_test_type)
    results = ci.shared("results")
    assert len(results) == 1
    result = results[0]
    payload = result.payload
    assert result.test_type == expected_results[0]
    assert result.overall_result == "PASSED"
    assert result.ids["rpmdiff_run_id"] == 118618
    assert result.urls["rpmdiff_url"] == "https://rpmdiff.engineering.redhat.com/run/118618"
    assert payload[0]["data"]["type"] == expected_results[1]
    assert payload[0]["data"]["item"] == expected_results[2]
    assert payload[0]["testcase"]["name"] == expected_results[3]
    assert payload[0]["outcome"] == "PASSED"
    assert payload[1]["testcase"]["name"] == expected_results[4]
    assert len(payload) == 32


def test_refresh_results_fail(module):
    _, module = module
    with pytest.raises(libci.CIError, match=r"^Cannot refresh old results, shared function \'results\' does not exist"):
        module.refresh_rpmdiff_results(118618)


def test_refresh_results_another_id(module_with_ciresults, monkeypatch):
    ci, module = module_with_ciresults
    monkeypatch.setattr(libci.utils, "run_command", failed_run_command)
    assert not ci.shared("results")
    module._publish_results(module._get_runinfo(118618), "analysis")
    results = ci.shared("results")
    result = results[0]
    assert len(results) == 1
    publish_mock = MagicMock()
    monkeypatch.setattr(module, "_publish_results", publish_mock)
    module.refresh_rpmdiff_results(1)
    results = ci.shared("results")
    assert len(results) == 1
    assert not publish_mock.called
    assert result == results[0]


def test_refresh_results(module_with_ciresults, monkeypatch):
    ci, module = module_with_ciresults
    monkeypatch.setattr(libci.utils, "run_command", failed_run_command)
    assert not ci.shared("results")
    module._publish_results(module._get_runinfo(118618), "analysis")
    results = ci.shared("results")
    assert len(results) == 1
    result = results[0]
    payload = result.payload
    assert result.test_type == "rpmdiff-analysis"
    assert result.overall_result == "FAILED"
    assert result.ids["rpmdiff_run_id"] == 118618
    assert payload[0]["outcome"] == "FAILED"
    monkeypatch.setattr(libci.utils, "run_command", passed_run_command)
    module.refresh_rpmdiff_results(118618)
    results = ci.shared("results")
    assert len(results) == 1
    result_refreshed = results[0]
    payload = result_refreshed.payload
    assert result != result_refreshed
    assert result_refreshed.test_type == "rpmdiff-analysis"
    assert result_refreshed.overall_result == "PASSED"
    assert result_refreshed.ids["rpmdiff_run_id"] == 118618
    assert payload[0]["outcome"] == "PASSED"


@pytest.mark.parametrize("url", [None, "url1"])
def test_execute_set_huburl(configured_module, url):
    _, module = configured_module
    module._config["url"] = url
    with pytest.raises(libci.CIError, match=r"^no brew build found"):
        module.execute()
    assert module.hub_url == url


def test_execute_no_brew_task(module):
    _, module = module
    with pytest.raises(libci.CIError, match=r"^no brew build found"):
        module.execute()


def test_execute_blacklisted(module_for_execute, log):
    _, module = module_for_execute
    module._config["blacklist"] = "dummy,which"
    module.execute()
    message = "skipping blacklisted package which"
    assert any(record.message == message for record in log.records)
    assert not module._publish_results.called


@pytest.mark.parametrize("baseline,expected_log_msg", [
    (None, "no baseline found, refusing to continue testing"),
    ("1.3", "cowardly refusing to compare same packages"),
])
def test_execute_comparison_silent_fail(module_for_execute, baseline, expected_log_msg, log):
    _, module = module_for_execute
    type(module.shared("brew_task")).latest = PropertyMock(return_value=baseline)
    module._config["type"] = "comparison"
    module.execute()
    assert any(record.message == expected_log_msg for record in log.records)
    assert not module._run_rpmdiff.called
    assert not module._get_runinfo.called
    assert not module._publish_results.called


def test_execute_with_runid(module_for_execute):
    _, module = module_for_execute
    module._config["run-id"] = 118618
    module.execute()
    assert not module._run_rpmdiff.called
    module._get_runinfo.assert_called_with(118618)
    module._publish_results.assert_called_with(module._get_runinfo(118618), "analysis")


@pytest.mark.parametrize("rpmdiff_test_type, expected_baseline", [
    ("comparison", "0.9"),
    ("analysis", "0.9"),
])
def test_execute(module_for_execute, rpmdiff_test_type, expected_baseline):
    _, module = module_for_execute
    module._config["type"] = rpmdiff_test_type
    module.execute()
    module._run_rpmdiff.assert_called_once_with(rpmdiff_test_type, expected_baseline)
    run_rpmdiff_result = module._run_rpmdiff(rpmdiff_test_type, expected_baseline)
    module._publish_results.assert_any_call(run_rpmdiff_result, rpmdiff_test_type)
