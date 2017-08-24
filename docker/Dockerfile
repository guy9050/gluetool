#
# citool docker image
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
    && pip install ansible virtualenv \
    && curl -k -o /etc/pki/ca-trust/source/anchors/RH-IT-Root-CA.crt https://password.corp.redhat.com/RH-IT-Root-CA.crt \
    && curl -k -o /etc/pki/ca-trust/source/anchors/Eng-CA.crt https://engineering.redhat.com/Eng-CA.crt \
    && update-ca-trust \
    && git clone https://gitlab.cee.redhat.com/baseos-qe/citool.git /tmp/citool \
    && virtualenv -p /usr/bin/python2.7 /opt/citool \
    && . /opt/citool/bin/activate \
    && cd /tmp/citool \
    && /usr/bin/ansible-playbook ./install.yml

# Some citool modules need qe.py. One way is to install qa-tools-workstation but that's
# quite a lot of dependencies...
#
# WIP:
# COPY qa-tools.repo /etc/yum.repos.d/qa-tools.repo
# COPY beaker-client.repo /etc/yum.repos.d/beaker-client.repo
# RUN yum install -y qa-tools-workstation
# RUN ln -s /usr/share/qa-tools/python-modules/qe.py /opt/citool/lib64/python2.7/site-packages

# Or inject it via volume...

COPY entrypoint.sh /entrypoint.sh
RUN chmod 700 /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
