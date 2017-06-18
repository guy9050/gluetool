import pytest

import libci

from . import NonLoadingCI


def _get_mod():
    from libci.modules.helpers.ansible import Ansible

    ci = NonLoadingCI()
    mod = Ansible(ci)
    mod.add_shared()

    return ci, mod


def test_sanity(tmpdir):
    ci, _ = _get_mod()

    assert ci.has_shared('run_playbook') is True

    playbook = tmpdir.join('sanity-playbook.yml')
    playbook.write("""---

- hosts: all
  remote_user: root
  connection: local

  tasks:
    - name: List current dir
      local_action: command ls -al .
      register: ls_output

    - debug: msg="{{ ls_output.stdout }}"
""")

    output = ci.shared('run_playbook', str(playbook), ['127.0.0.1'])
    assert isinstance(output, libci.utils.ProcessOutput)

    assert output.exit_code == 0
    assert 'ok=3' in output.stdout
    assert 'changed=1' in output.stdout
    assert 'unreachable=0' in output.stdout
    assert 'failed=0' in output.stdout
    assert output.stderr == ''


def test_error(log, tmpdir):
    ci, _ = _get_mod()

    playbook = tmpdir.join('error-playbook.yml')
    playbook.write("""---

- hosts: all
  remote_user: root
  connection: local

  tasks:
    - name: Check env FOO_VAR is defined
      fail:
        msg: "FOO_VAR variable is not defined"
      when: FOO_VAR is not defined
""")

    with pytest.raises(libci.CIError, message='Failure during Ansible playbook execution. See log for details.'):
        ci.shared('run_playbook', str(playbook), ['127.0.0.1'])

    assert log.records[-1].message == 'Ansible says: FOO_VAR variable is not defined'


def test_extra_vars(tmpdir):
    ci, _ = _get_mod()

    playbook = tmpdir.join('extra-vars-playbook.yml')
    playbook.write("""---

- hosts: all
  remote_user: root
  connection: local

  tasks:
    - name: Check env FOO_VAR is defined
      fail:
        msg: "FOO_VAR variable is not defined"
      when: FOO_VAR is not defined

    - debug: msg="{{ FOO_VAR }}"
""")

    output = ci.shared('run_playbook', str(playbook), ['127.0.0.1'], variables={
        'FOO_VAR': 'This should appear in Ansible output'
    })

    assert '"msg": "This should appear in Ansible output"' in output.stdout
