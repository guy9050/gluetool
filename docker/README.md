# Docker image


## Building

```
  docker build -t citool .
```


## Running

```
  docker run -it --rm citool -h
```

## How?

* the image is based on CentOS base image
* `citool` is installed inside its own virtualenv, `/opt/citool`
* image's entry point is a simple shell script which activates virtualenv and passes all command-line arguments
  to `citool`


## Known issues

#### Missing configuration

So far, there's no configuration for `citool` and its modules. One viable way might be to simply inject your own
using a volume:

```
  -v /home/foo/.citool.d:/etc/citool.d
```


#### `qe.py` dependency

Some modules (e.g. `beaker`) require `qe` module to work correctly. This module is provided by `qa-tools-workstation`
which, unfortunately, has quite a lot of requirements on its own. One solution would be to install `qa-tools-workstation`
into the image, with all of its requirements, another way'd be simply injecting your own copy using volumes:

```
  -v /usr/share/qa-tools/python-modules/qe.py:/opt/citool/lib64/python2.7/site-packages/qe.py
```


#### External commands

Many modules use external commands to do the actual work (e.g. `rpmdiff` uses `rpmdiff-remote`), and these commands
are obviously *not* installed in the image. You'd have to provide them via volume, and probably update the `PATH`.
No final solution was proposed so far.
