#!/bin/bash

set -e
cd $(mktemp -d)
/usr/bin/git clone --depth 1 --branch master git@github.com:clerkio/clerk.io.git
#date > clerk.io/src/version.txt
#git -C clerk.io rev-parse HEAD >> clerk.io/src/version.txt
/usr/bin/docker build -t clerk.io clerk.io
systemctl restart clerk.io
rm -rf $(pwd)
