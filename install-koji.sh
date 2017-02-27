#!/bin/sh

python -c 'import koji' 2>&1 | grep 'No module named koji' &> /dev/null

if [ "$?" = "1" ]; then
  echo "koji module already installed"
  exit 0
fi

rm -rf .koji
git clone https://pagure.io/koji.git ./.koji && pushd ./.koji/koji && make install && popd
