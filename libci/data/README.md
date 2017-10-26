# BaseOS CI pipelines

## General rules

Please, follow (and update) these rules when it comes to BaseOS CI pipelines and their JJB definitions.

### Archive all (important) artifacts

Archive artifacts, by adding a ``archive`` key under ``publishers`` section:

```
    publishers:
      ...
      - archive:
        artifacts: '**/**'
        ( or artifacts: '*.txt, *.html, *.json')
        allow-empty: 'true'
```

### Remove old builds

```
    properties:
      ...
      - build-discarder:
          num-to-keep: 500
```

### Colorized console

This is **very** useful!

```
    wrappers:
      - ansicolor:
          colormap: xterm
```

### Clear workspace before beginning

```
    wrappers:
      - workspace-cleanup
```

### Make debugging easier

Use colors, dump the command line, and always provide debug log, for more detailed information:

```
    citool -c -i -o citool-debug.txt \
    ...
```

### Support build timeouts

Add `timeout_duration` parameter, and use it in `shell` step:

```
    timeout --preserve-status --foreground --signal=SIGTERM ${timeout_duration} \
    citool -c -i -o citool-debug.txt \
    ...
```

### Support pipeline add-ons

Let users add modules to the both ends of your pipeline, by adding `pipeline_append` and `pipeline_prepend` parameters:

```
    citool -c -i -o citool-debug.txt \
      ${pipeline_prepend} \
      ...
      ${pipeline_append}
```

### Order of the modules

Modules have dependencies, CI teams have requirements, but this is, in general, an example of what our current pipeline look like:

```
    timeout --preserve-status --foreground --signal=SIGTERM ${timeout_duration} \
    citool -c -i -o citool-debug.txt \
        ${pipeline_prepend} \
        notify-recipients ${notify_recipients_options} \
        testing-results \
        testing-thread --id "${testing_thread_id}" \
        brew --task-id $id \
        jenkins \
        brew-build-name \
        publisher-umb-bus \
        pipeline-state-reporter --category=rpmdiff ${pipeline_state_reporter_options} \
        ansible \
        restraint \
        guest-setup --playbooks=${CITOOL_CONFIG}/guest-setup/openstack-restraint.yaml \
        wow --wow-options="${wow_options}" \
        beah-result-parser \
        openstack ${openstack_opts} \
        guess-product ${guess_product_options} \
        guess-beaker-distro ${guess_beaker_distro_options} \
        guess-openstack-image ${guess_openstack_image_options} \
        restraint-scheduler \
        restraint-runner ${restraint_runner_options} \
        notify-email --add-frontend-url ${notify_email_options} ${notify_email_opts} \
        ${pipeline_append}
```

First lines we can call a "standard" header of BaseOS CI pipelines:

```
    timeout --preserve-status --foreground --signal=SIGTERM ${timeout_duration} \
    citool -c -i -o citool-debug.txt \
        ${pipeline_prepend} \
        notify-recipients ${notify_recipients_options} \
        testing-results \
        testing-thread --id "${testing_thread_id}" \
        brew --task-id $id \
        jenkins \
        brew-build-name \
        publisher-umb-bus \
        pipeline-state-reporter --category=rpmdiff ${pipeline_state_reporter_options}
```

#### `notify-recipients` must stand at the beginning

Should there be any exceptions during the run time, a modules might need to notify users about this. This usually happens in `destroy` methods, therefore `notify-recipients` must be placed as close as possible to the beginning, to get its shared functions published.

#### `testing-results` precedes `testing-thread`

`testing-thread` modifies gathered results, therefore `testing-results` must come first:

```
    ....
    testing-results \
    testing-thread --id "${testing_thread_id}" \
    ...
```

#### Name the build as soon as possible

Many modules may kill the pipeline, therefore it'd be good to name the build rather sooner then later, to know to what Brew/Koji/... build this build relates.

```
    ...
    brew --task-id $id \
    jenkins \
    brew-build-name \
    ...
```

#### Pipeline state reporting

Report the progress of your CI pipeline, ideally on the message bus. Provide the correct category, accept additional options.

```
        publisher-umb-bus \
        pipeline-state-reporter --category=rpmdiff ${pipeline_state_reporter_options} \
```

#### Support modules

Then list "support" modules - bits that don't much on their on in their `execute` modules, but provide their shared functions to those who come after them.

```
    ...
    wow --wow-options="${wow_options}" \
    beah-result-parser \
    guess-product ${guess_product_options} \
    ...
```

#### The  actual "testing" work

Next are modules that do the "testing" - those, who ask other modules for their information, and run the testing tasks.

```
    ...
    beaker --jobwatch-options="${jobwatch_options}" ${reserve_opts} \
    ...
```

Or:

```
    ...
    restraint-scheduler \
    restraint-runner ${restraint_runner_options} \
    ...
```

#### Final steps

The rest is up to you, usually we want to just notify users:

```
    ...
    notify-email --add-frontend-url ${notify_email_options} ${notify_email_opts} \
    ...
```
