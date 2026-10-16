#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y build-essential bc bison flex git git-lfs gnupg curl zip unzip \
  rsync cpio python-is-python3 python3-pip libssl-dev libncurses5 libncurses5-dev \
  lib32ncurses-dev lib32z1-dev libc6-dev-i386 libxml2-utils xsltproc lz4 lzop \
  device-tree-compiler imagemagick fontconfig openjdk-11-jdk-headless wget file locales repo
