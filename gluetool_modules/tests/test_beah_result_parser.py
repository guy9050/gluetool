# pylint: disable=too-many-arguments

import pytest

import gluetool_modules.testing.beah_result_parser

from . import create_module, check_loadable, xml as X


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.testing.beah_result_parser.BeahResultParser)


def test_sanity(module):
    _, _ = module


def test_loadable(module):
    glue, _ = module

    check_loadable(glue, 'gluetool_modules/testing/beah_result_parser.py', 'BeahResultParser')


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        X('<task />'),
        None,
        None,
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST><arch>  journal-arch  </arch></BEAKER_TEST>'),
        None,
        'journal-arch'
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST><arch>  journal-arch  </arch></BEAKER_TEST>'),
        X('<recipe />'),
        'journal-arch'
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST><arch>  journal-arch  </arch></BEAKER_TEST>'),
        X('<recipe arch="recipe-arch" />'),
        'recipe-arch'
    )
])
def test_arch(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_architecture(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_arch' not in result
        assert log.records[-1].message == 'Cannot deduce architecture'

    else:
        assert result['bkr_arch'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        None,
        None,
        X('<recipe />'),
        None
    ),
    (
        None,
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        None
    ),
    (
        X('<task version="1.0-140" />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        '1.0-140'
    )
])
def test_version(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_version(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_version' not in result
        assert log.records[-1].message == 'Cannot deduce bkr version'

    else:
        assert result['bkr_version'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        X('<task />'),
        None,
        None,
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        None,
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe id="17" />'),
        17
    )
])
def test_recipe_id(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_recipe_id(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_recipe_id' not in result
        assert log.records[-1].message == 'Cannot deduce recipe ID'

    else:
        assert result['bkr_recipe_id'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        X('<task />'),
        None,
        None,
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        None,
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe   >'),
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe distro="RHEL-7.3" />'),
        'RHEL-7.3'
    )
])
def test_distro(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_distro(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_distro' not in result
        assert log.records[-1].message == 'Cannot deduce recipe distro'

    else:
        assert result['bkr_distro'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        X('<task />'),
        None,
        None,
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        None,
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe variant="Server" />'),
        'Server'
    )
])
def test_variant(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_variant(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_variant' not in result
        assert log.records[-1].message == 'Cannot deduce recipe variant'

    else:
        assert result['bkr_variant'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        None,
        None,
        X('<recipe />'),
        None
    ),
    (
        None,
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        None
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        None
    ),
    (
        X('<task><params><param name="FOO" value="bar"/><param name="BAZ" value=""/></params></task>'),
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        ['FOO="bar"', 'BAZ=""']
    )
])
def test_params(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_params(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_params' not in result
        assert log.records[-1].message == 'Cannot deduce task parameters'

    else:
        assert result['bkr_params'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        None,
        None,
        X('<recipe />'),
        None
    ),
    (
        X('<task/>'),
        None,
        X('<recipe />'),
        None
    ),
    (
        X('<task/>'),
        X('<BEAKER_TEST />'),
        X('<recipe />'),
        []
    ),
    (
        X('<task/>'),
        X("""<BEAKER_TEST>
               <log>
                 <phase>
                   <pkgdetails sourcerpm="foo-1.2.el7.src.rpm">foo-1.2.el7.x86_64</pkgdetails>
                   <pkgdetails sourcerpm="bar-3.4.el6.src.rpm">bar-3.4.el6.s390x</pkgdetails>
                 </phase>
               </log>
             </BEAKER_TEST>"""),
        X('<recipe />'),
        [u'bar-3.4.el6.s390x', u'bar-3.4.el6.src.rpm', u'foo-1.2.el7.src.rpm', u'foo-1.2.el7.x86_64']
    )
])
def test_packages(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_packages(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_packages' not in result
        assert log.records[-1].message == 'Cannot deduce involved packages'

    else:
        assert result['bkr_packages'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        None,
        None,
        X('<recipe />'),
        None
    ),
    (
        None,
        X('<BEAKER_TEST></BEAKER_TEST>'),
        X('<recipe />'),
        None
    ),
    (
        None,
        X('<BEAKER_TEST><hostname>   foo.bar.baz </hostname></BEAKER_TEST>'),
        X('<recipe />'),
        'foo.bar.baz'
    ),
    (
        X('<task />'),
        X('<BEAKER_TEST><hostname>   foo.bar.baz </hostname></BEAKER_TEST>'),
        X('<recipe />'),
        'foo.bar.baz'
    ),
    (
        X('<task><roles><role /></roles></task>'),
        X('<BEAKER_TEST><hostname>   foo.bar.baz </hostname></BEAKER_TEST>'),
        X('<recipe />'),
        'foo.bar.baz'
    ),
    (
        X('<task><roles><role><system value="baz.bar.foo" /></role></roles></task>'),
        X('<BEAKER_TEST><hostname>   foo.bar.baz </hostname></BEAKER_TEST>'),
        X('<recipe />'),
        'baz.bar.foo'
    )
])
def test_machine(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_machine(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_host' not in result
        assert log.records[-1].message == 'Cannot deduce hostname'

    else:
        assert result['bkr_host'] == expected


@pytest.mark.parametrize('connectable_hostname, result, expected', [
    (
        None,
        {'bkr_host': 17},
        17
    ),
    (
        19,
        {'bkr_host': 17},
        19
    )
])
def test_connectable_host(module, connectable_hostname, result, expected):
    _, mod = module

    # pylint: disable=protected-access
    mod._find_connectable_host(result, connectable_hostname)

    assert result['connectable_host'] == expected


@pytest.mark.parametrize('task, journal, recipe, expected', [
    (None, None, None, None),
    (
        None,
        None,
        X('<recipe />'),
        None
    ),
    (
        None,
        X('<BEAKER_TEST />'),
        X('<recipe />'),
        None
    ),
    (
        None,
        X("""<BEAKER_TEST>
               <starttime>  2017-07-04 10:58:03 EDT  </starttime>
               <endtime>  2017-07-05 11:08:10 EDT  </endtime>
             </BEAKER_TEST>"""),
        X('<recipe />'),
        87007
    ),
    (
        X('<task />'),
        X("""<BEAKER_TEST>
               <starttime>  2017-07-04 10:58:03 EDT  </starttime>
               <endtime>  2017-07-05 11:08:10 EDT  </endtime>
             </BEAKER_TEST>"""),
        X('<recipe />'),
        87007
    ),
    (
        X('<task duration="1 day, 23:51:43"/>'),
        X("""<BEAKER_TEST>
               <starttime>  2017-07-04 10:58:03 EDT  </starttime>
               <endtime>  2017-07-05 11:08:10 EDT  </endtime>
             </BEAKER_TEST>"""),
        X('<recipe />'),
        172303
    )
])
def test_duration(log, module, task, journal, recipe, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_duration(result, task, journal, recipe)

    if expected is None:
        assert 'bkr_duration' not in result
        assert log.records[-1].message == 'Cannot deduce task duration'

    else:
        assert result['bkr_duration'] == expected


@pytest.mark.parametrize('element, expected', [
    (X('<task />'), []),
    (X('<task><logs /></task>'), []),
    (
        X("""<task>
               <logs>
                 <log href="http://foo.com/bar" name="some log" />
                 <log href="http://bar.com/foo" name="another log" />
               </logs>
             </task>"""),
        [
            {'name': 'some log', 'href': 'http://foo.com/bar'},
            {'name': 'another log', 'href': 'http://bar.com/foo'}
        ]
    ),
    (
        X("""<task>
               <logs>
                 <log path="http://foo.com/bar" filename="some log" />
                 <log path="http://bar.com/foo" filename="another log" />
               </logs>
             </task>"""),
        [
            {'name': 'some log', 'href': 'http://foo.com/bar'},
            {'name': 'another log', 'href': 'http://bar.com/foo'}
        ]
    )
])
def test_logs(module, element, expected):
    _, mod = module

    # pylint: disable=protected-access
    logs = mod._find_logs(element, None)

    assert logs == expected


@pytest.mark.parametrize('task, journal, expected', [
    (None, None, None),
    (
        None,
        None,
        None
    ),
    (
        None,
        X('<BEAKER_TEST />'),
        None
    ),
    (
        None,
        X('<BEAKER_TEST><log /></BEAKER_TEST>'),
        []
    ),
    (
        None,
        X("""<BEAKER_TEST>
               <log>
                 <phase name="phase #1" result="result #1">
                   <logs>
                     <log path="http://foo.com/bar" filename="some log" />
                     <log path="http://bar.com/foo" filename="another log" />
                   </logs>
                 </phase>
                 <phase name="phase #2" result="result #2">
                   <logs>
                     <log path="http://foo.com/bar/2" filename="some log #2" />
                     <log path="http://bar.com/foo/2" filename="another log #2" />
                   </logs>
                 </phase>
               </log>
             </BEAKER_TEST>"""),
        [
            {
                'name': 'phase #1',
                'result': 'result #1',
                'logs': [
                    {'name': 'some log', 'href': 'http://foo.com/bar'},
                    {'name': 'another log', 'href': 'http://bar.com/foo'}
                ]
            },
            {
                'name': 'phase #2',
                'result': 'result #2',
                'logs': [
                    {'name': 'some log #2', 'href': 'http://foo.com/bar/2'},
                    {'name': 'another log #2', 'href': 'http://bar.com/foo/2'}
                ]
            }
        ]
    ),
    (
        X('<task/>'),
        X("""<BEAKER_TEST>
               <log>
                 <phase name="phase #1" result="result #1">
                   <logs>
                     <log path="http://foo.com/bar" filename="some log" />
                     <log path="http://bar.com/foo" filename="another log" />
                   </logs>
                 </phase>
                 <phase name="phase #2" result="result #2">
                   <logs>
                     <log path="http://foo.com/bar/2" filename="some log #2" />
                     <log path="http://bar.com/foo/2" filename="another log #2" />
                   </logs>
                 </phase>
               </log>
             </BEAKER_TEST>"""),
        [
            {
                'name': 'phase #1',
                'result': 'result #1',
                'logs': [
                    {'name': 'some log', 'href': 'http://foo.com/bar'},
                    {'name': 'another log', 'href': 'http://bar.com/foo'}
                ]
            },
            {
                'name': 'phase #2',
                'result': 'result #2',
                'logs': [
                    {'name': 'some log #2', 'href': 'http://foo.com/bar/2'},
                    {'name': 'another log #2', 'href': 'http://bar.com/foo/2'}
                ]
            }
        ]
    ),
    (
        X("""<task>
               <results>
                 <result path="phase #3" result="result #3">
                   <logs>
                     <log path="http://foo.com/baz" filename="some log #3" />
                     <log path="http://bar.com/baz" filename="another log #4" />
                   </logs>
                 </phase>
                 <result path="phase #4" result="result #4">
                   <logs>
                     <log path="http://foo.com/baz/2" filename="some log #5" />
                     <log path="http://bar.com/baz/2" filename="another log #6" />
                   </logs>
                 </phase>
               </results>
             </task>"""),
        X("""<BEAKER_TEST>
               <log>
                 <phase name="phase #1" result="result #1">
                   <logs>
                     <log path="http://foo.com/bar" filename="some log" />
                     <log path="http://bar.com/foo" filename="another log" />
                   </logs>
                 </phase>
                 <phase name="phase #2" result="result #2">
                   <logs>
                     <log path="http://foo.com/bar/2" filename="some log #2" />
                     <log path="http://bar.com/foo/2" filename="another log #2" />
                   </logs>
                 </phase>
               </log>
             </BEAKER_TEST>"""),
        [
            {
                'name': 'phase #3',
                'result': 'result #3',
                'logs': [
                    {'name': 'some log #3', 'href': 'http://foo.com/baz'},
                    {'name': 'another log #4', 'href': 'http://bar.com/baz'}
                ]
            },
            {
                'name': 'phase #4',
                'result': 'result #4',
                'logs': [
                    {'name': 'some log #5', 'href': 'http://foo.com/baz/2'},
                    {'name': 'another log #6', 'href': 'http://bar.com/baz/2'}
                ]
            }
        ]
    )
])
def test_phases(log, module, task, journal, expected):
    _, mod = module

    result = {}

    # pylint: disable=protected-access
    mod._find_phases(result, task, journal, None)

    if expected is None:
        assert 'bkr_phases' not in result
        assert log.records[-1].message == 'Cannot deduce task phases'

    else:
        assert result['bkr_phases'] == expected


def test_parser(module):
    _, mod = module

    task = X("""
        <task id="113" duration="1 day, 23:56:24" name="/distribution/install" result="Pass" status="Completed" version="1.12-2">
          <roles>
            <role value="STANDALONE">
              <system value="foo.bar.com"/>
            </role>
          </roles>
          <params>
            <param name="foo_param" value="bar_value"/>
          </params>
          <logs>
            <log path="http://foo.com/bar/" filename="some log" />
            <log path="http://bar.com/foo/" filename="another log" />
          </logs>
          <results>
            <result path="/phase1" result="Pass" score="0.00">
              (Pass)
              <logs>
                <log href="https://foo.cz/" name="phase-1-log"/>
              </logs>
            </result>
          </results>
        </task>
    """)

    journal = X("""
        <BEAKER_TEST>
          <pkgdetails sourcerpm="foo-1.2.el6.src.rpm">
            foo-1.2.el6.x86_64
          </pkgdetails>
        </BEAKER_TEST>
    """)

    recipe = X("""
        <recipe arch="x86_64" distro="RHEL-7.3" id="4026369" variant="Client">
        </recipe>
    """)

    # excercise debug logging
    mod.parse_beah_result(task)

    # now run it for real
    result = mod.parse_beah_result(task, journal=journal, recipe=recipe,
                                   connectable_hostname='connectable-foo.bar.com')

    assert result == {
        'name': '/distribution/install',
        'bkr_arch': 'x86_64',
        'bkr_distro': 'RHEL-7.3',
        'bkr_variant': 'Client',
        'bkr_duration': 172584,
        'bkr_host': 'foo.bar.com',
        'connectable_host': 'connectable-foo.bar.com',
        'bkr_logs': [
            {'href': 'http://foo.com/bar/', 'name': 'some log'},
            {'href': 'http://bar.com/foo/', 'name': 'another log'}
        ],
        'bkr_packages': ['foo-1.2.el6.src.rpm', 'foo-1.2.el6.x86_64'],
        'bkr_params': [
            'foo_param="bar_value"'
        ],
        'bkr_phases': [
            {
                'logs': [
                    {'href': 'https://foo.cz/', 'name': 'phase-1-log'}
                ],
                'name': '/phase1',
                'result': 'Pass'
            }
        ],
        'bkr_recipe_id': 4026369,
        'bkr_result': 'Pass',
        'bkr_status': 'Completed',
        'bkr_task_id': 113,
        'bkr_version': '1.12-2'
    }
