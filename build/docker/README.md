citool docker images
--------------------

You have to provide your own definitions of `citool` flavors, in an YAML file, for example:

```
$ cat citool-flavors.yml
core:
  module_dirs:
    - type: directory
      path: /abs/path/to/citool/libci
    - type: git
      repository: https://foo.com/bar.git
      version: master
  modules:
    - citool-core
    - citool-documentation
$ 
```

So far, as a base image, only `centos:7` is supported.

Then, you can run the playbook that's going to build your customized image:

```
  /usr/bin/ansible-playbook -e citool_flavors=/path/to/citool-flavors.yml -e citool_flavor=core build/docker/build.yml
```
