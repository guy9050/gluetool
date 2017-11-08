#
# Image used for testing citool in Gitlab's CI
#

FROM centos:7

MAINTAINER BaseOS CI <baseos-ci@redhat.com>

RUN    yum install -y ca-certificates \
                      gcc \
                      git \
                      krb5-devel \
                      libcurl-devel \
                      libxml2-devel \
                      make \
                      openssl-devel \
                      postgresql-devel \
                      python-devel \
                      python-rpm \
                      python-setuptools \
    && curl "https://bootstrap.pypa.io/get-pip.py" -o "/tmp/get-pip.py" \
    && python /tmp/get-pip.py \
    && rm -f /tmp/get-pip.py \
    && pip install -U setuptools \
    && pip install ansible virtualenv nitrate python-dateutil \
    && curl -k -o /etc/pki/ca-trust/source/anchors/RH-IT-Root-CA.crt https://password.corp.redhat.com/RH-IT-Root-CA.crt \
    && curl -k -o /etc/pki/ca-trust/source/anchors/Eng-CA.crt https://engineering.redhat.com/Eng-CA.crt \
    && update-ca-trust \
    && yum clean metadata \
    && mkdir -p /usr/share/qa-tools/python-modules \
    && curl -Lko /usr/share/qa-tools/python-modules/qe.py "https://code.engineering.redhat.com/gerrit/gitweb?p=apps.git;a=blob_plain;f=qe.py;hb=HEAD"

COPY contrib/pip.conf /etc/pip.conf
COPY contrib/.pydistutils.cfg /root/.pydistutils.cfg
