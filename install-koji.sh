#!/bin/sh

python -c 'import koji' 2>&1 | grep 'No module named koji' &> /dev/null

if [ "$?" = "1" ]; then
  echo "koji module already installed"
  exit 0
fi

rm -rf .koji
git clone https://pagure.io/koji.git ./.koji && pushd ./.koji/koji && make install && popd

# create symlink to rpm if virtualenv detected
[ -n "$VIRTUAL_ENV" ] && ln -sf /usr/lib64/python2.7/site-packages/rpm $VIRTUAL_ENV/lib64/python2.7/site-packages
