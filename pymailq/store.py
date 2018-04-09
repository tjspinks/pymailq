# coding: utf-8
#
#    Postfix queue control python tool (pymailq)
#
#    Copyright (C) 2014 Denis Pompilio (jawa) <denis.pompilio@gmail.com>
#
#    This file is part of pymailq
#
#    This program is free software; you can redistribute it and/or
#    modify it under the terms of the GNU General Public License
#    as published by the Free Software Foundation; either version 2
#    of the License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import sys
import os
import gc
import re
import subprocess
import email
from email import header
from collections import Counter
from datetime import datetime, timedelta
from pymailq import CONFIG, debug


class MailHeaders(object):
    """
    Simple object to store mail headers.

    Object's attributes are dynamically created when parent :class:`~store.Mail`
    object's method :meth:`~store.Mail.parse` is called. Those attributes are
    retrieved with help of :func:`~email.message_from_string` method provided
    by the :mod:`email` module.

    Standard RFC *822-style* mail headers becomes attributes including but not
    limited to:

    - :mailheader:`Received`
    - :mailheader:`From`
    - :mailheader:`To`
    - :mailheader:`Cc`
    - :mailheader:`Bcc`
    - :mailheader:`Sender`
    - :mailheader:`Reply-To`
    - :mailheader:`Subject`

    Case is kept while creating attribute and access will be made with
    :attr:`Mail.From` or :attr:`Mail.Received` for example. All those
    attributes will return *list* of values.

    .. seealso::

        Python modules:
            :mod:`email` -- An email and MIME handling package

            :class:`email.message.Message` -- Representing an email message

        :rfc:`822` -- Standard for ARPA Internet Text Messages
    """


class Mail(object):
    """
    Simple object to manipulate email messages.

    This class provides the necessary methods to load and inspect mails
    content. This object functionnalities are mainly based on :mod:`email`
    module's provided class and methods. However,
    :class:`email.message.Message` instance's stored informations are
    extracted to extend :class:`~store.Mail` instances attributes.

    Initialization of :class:`~store.Mail` instances are made the following
    way:

    :param str mail_id: Mail's queue ID string
    :param int size: Mail size in Bytes (Default: ``0``)
    :param datetime.datetime date:  Acceptance date and time in mails queue.
                                    (Default: :data:`None`)
    :param str sender: Mail sender string as seen in mails queue.
                       (Default: empty :func:`str`)

    The :class:`~pymailq.Mail` class defines the following attributes:

        .. attribute:: qid

            Mail Postfix queue ID string, validated by
            :meth:`~store.PostqueueStore._is_mail_id` method.

        .. attribute:: size

            Mail size in bytes. Expected type is :func:`int`.

        .. attribute:: parsed

            :func:`bool` value to track if mail's content has been loaded from
            corresponding spool file.

        .. attribute:: parse_error

            Last encountered parse error message :func:`str`.

        .. attribute:: date

            :class:`~datetime.datetime` object of acceptance date and time in
            mails queue.

        .. attribute:: status

            Mail's queue status :func:`str`.

        .. attribute:: sender

            Mail's sender :func:`str` as seen in mails queue.

        .. attribute:: recipients

            Recipients :func:`list` as seen in mails queue.

        .. attribute:: errors

            Mail deliver errors :func:`list` as seen in mails queue.

        .. attribute:: head

            Mail's headers :class:`~store.MailHeaders` structure.

        .. attribute:: postcat_cmd

            This property use Postfix mails content parsing command defined in
            :attr:`pymailq.CONFIG` attribute under the key 'cat_message'.
            Command and arguments list is build on call with the configuration
            data.

            .. seealso::

                :ref:`pymailq-configuration`
    """

    def __init__(self, mail_id, size=0, date=None, sender=""):
        """Init method"""
        self.parsed = False
        self.parse_error = ""
        self.qid = mail_id
        self.date = date
        self.status = ""
        self.size = int(size)
        self.sender = sender
        self.recipients = []
        self.errors = []
        self.head = MailHeaders()

        # Getting optionnal status from postqueue mail_id
        postqueue_status = {'*': "active", '!': "hold"}
        if mail_id[-1] in postqueue_status:
            self.qid = mail_id[:-1]
        self.status = postqueue_status.get(mail_id[-1], "deferred")

    @property
    def postcat_cmd(self):
        """
        Get the cat_message command from configuration
        :return: Command as :class:`list`
        """
        postcat_cmd = CONFIG['commands']['cat_message'] + [self.qid]
        if CONFIG['commands']['use_sudo']:
            postcat_cmd.insert(0, 'sudo')
        return postcat_cmd

    def show(self):
        """
        Return mail detailled representation for printing

        :return: Representation as :class:`str`
        """
        output = "=== Mail %s ===\n" % (self.qid,)
        for attr in sorted(dir(self.head)):
            if attr.startswith("_"):
                continue

            value = getattr(self.head, attr)
            if not isinstance(value, str):
                value = ", ".join(value)

            if attr == "Subject":
                print(attr, value)
                value, enc = header.decode_header(value)[0]
                print(enc, attr, value)
                if sys.version_info[0] == 2:
                    value = value.decode(enc) if enc else unicode(value)

            output += "%s: %s\n" % (attr, value)
        return output

    @debug
    def parse(self):
        """
        Parse message content.

        This method use Postfix mails content parsing command defined in
        :attr:`~Mail.postcat_cmd` attribute.
        This command is runned using :class:`subprocess.Popen` instance.

        Parsed headers become attributes and are retrieved with help of
        :func:`~email.message_from_string` function provided by the
        :mod:`email` module.

        .. seealso::

            Postfix manual:
                `postcat`_ -- Show Postfix queue file contents

        """
        # Reset parsing error message
        self.parse_error = ""

        child = subprocess.Popen(self.postcat_cmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        stdout, stderr = child.communicate()

        if not len(stdout):
            # Ignore first 3 line on stderr which are:
            #   postcat: name_mask: all
            #   postcat: inet_addr_local: configured 3 IPv4 addresses
            #   postcat: inet_addr_local: configured 3 IPv6 addresses
            self.parse_error = "\n".join(stderr.decode().split('\n')[3:])
            return

        raw_content = ""
        for line in stdout.decode('utf-8', errors='replace').split('\n'):
            if self.size == 0 and line.startswith("message_size: "):
                self.size = int(line[14:].strip().split()[0])
            elif self.date is None and line.startswith("create_time: "):
                self.date = datetime.strptime(line[13:].strip(),
                                              "%a %b %d %H:%M:%S %Y")
            elif not len(self.sender) and line.startswith("sender: "):
                self.sender = line[8:].strip()
            elif line.startswith("regular_text: "):
                raw_content += "%s\n" % (line[14:],)

        # For python2.7 compatibility, encode unicode to str
        if not isinstance(raw_content, str):
            raw_content = raw_content.encode('utf-8')

        message = email.message_from_string(raw_content)

        for mailheader in set(message.keys()):
            value = message.get_all(mailheader)
            setattr(self.head, mailheader, value)

        self.parsed = True

    @debug
    def dump(self):
        """
        Dump mail's gathered informations to a :class:`dict` object.

        Mails informations are splitted in two parts in dictionnary.
        ``postqueue`` key regroups every informations directly gathered from
        Postfix queue, while ``headers`` regroups :class:`~store.MailHeaders`
        attributes converted from mail content with the
        :meth:`~store.Mail.parse` method.

        If mail has not been parsed with the :meth:`~store.Mail.parse` method,
        informations under the ``headers`` key will be empty.

        :return: Mail gathered informations
        :rtype: :class:`dict`
        """
        datas = {'postqueue': {},
                 'headers': {}}

        for attr in self.__dict__:
            if attr[0] != "_" and attr != 'head':
                datas['postqueue'].update({attr: getattr(self, attr)})

        for mailheader in self.head.__dict__:
            if mailheader[0] != "_":
                datas['headers'].update(
                    {mailheader: getattr(self.head, mailheader)}
                )

        return datas


class PostqueueStore(object):
    """
    Postfix mails queue informations storage.

    The :class:`~store.PostqueueStore` provides methods to load Postfix
    queued mails informations into Python structures. Thoses structures are
    based on :class:`~store.Mail` and :class:`~store.MailHeaders` classes
    which can be processed by a :class:`~selector.MailSelector` instance.

    The :class:`~store.PostqueueStore` class defines the following attributes:

        .. attribute:: mails

            Loaded :class:`MailClass` objects :func:`list`.

        .. attribute:: loaded_at

            :class:`datetime.datetime` instance to store load date and time
            informations, useful for datas deprecation tracking. Updated on
            :meth:`~store.PostqueueStore.load` call with
            :meth:`datetime.datetime.now` method.

        .. attribute:: postqueue_cmd

            :obj:`list` object to store Postfix command and arguments to view
            the mails queue content. This property use Postfix mails content
            parsing command defined in :attr:`pymailq.CONFIG` attribute under
            the key 'list_queue'. Command and arguments list is build on call
            with the configuration data.

        .. attribute:: spool_path

            Postfix spool path string.
            Default is ``"/var/spool/postfix"``.

        .. attribute:: postqueue_mailstatus

            Postfix known queued mail status list.
            Default is ``['active', 'deferred', 'hold']``.

        .. attribute:: mail_id_re

            Python compiled regular expression object (:class:`re.RegexObject`)
            provided by :func:`re.compile` method to match postfix IDs.
            Recognized IDs are either:
                - hexadecimals, 8 to 12 chars length (regular queue IDs)
                - encoded in a 52-character alphabet, minimum 11 chars length
                  (long queue IDs)
            They can be followed with ``*`` or ``!``.
            Default used regular expression is:
                ``r"^([A-F0-9]{8,12}|[B-Zb-z0-9]{11,})[*!]?$"``.

        .. attribute:: mail_addr_re

            Python compiled regular expression object (:class:`re.RegexObject`)
            provided by :func:`re.compile` method to match email addresses.
            Default used regular expression is:
            ``r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$"``

        .. attribute:: MailClass

            The class used to manipulate/parse mails individually.
            Default is :class:`~store.Mail`.

    .. seealso::

        Python modules:
            :mod:`datetime` -- Basic date and time types

            :mod:`re` -- Regular expression operations

        Postfix manual:
            `postqueue`_ -- Postfix queue control

        :rfc:`3696` -- Checking and Transformation of Names
    """
    postqueue_cmd = None
    spool_path = None
    postqueue_mailstatus = ['active', 'deferred', 'hold']
    mail_id_re = re.compile(r"^([A-F0-9]{8,12}|[B-Zb-z0-9]{11,})[*!]?$")
    mail_addr_re = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$")
    MailClass = Mail

    def __init__(self):
        """Init method"""
        self.spool_path = CONFIG['core']['postfix_spool']
        self.postqueue_cmd = CONFIG['commands']['list_queue']
        if CONFIG['commands']['use_sudo']:
            self.postqueue_cmd.insert(0, 'sudo')

        self.loaded_at = None
        self.mails = []

    @property
    @debug
    def known_headers(self):
        """Return known headers from loaded mails

        :return: headers as :func:`set`
        """
        headers = set()
        for mail in self.mails:
            for mailheader in dir(mail.head):
                if not mailheader.startswith("_"):
                    headers.add(mailheader)
        return headers

    @debug
    def _get_postqueue_output(self):
        """
        Get Postfix postqueue command output.

        This method used the postfix command defined in
        :attr:`~PostqueueStore.postqueue_cmd` attribute to view the mails queue
        content.

        Command defined in :attr:`~PostqueueStore.postqueue_cmd` attribute is
        runned using a :class:`subprocess.Popen` instance.

        :return: Command's output lines.
        :rtype: :func:`list`

        .. seealso::

            Python module:
                :mod:`subprocess` -- Subprocess management
        """
        child = subprocess.Popen(self.postqueue_cmd,
                                 stdout=subprocess.PIPE)
        stdout = child.communicate()[0]

        # return lines list without the headers and footers
        return [line.strip() for line in stdout.decode().split('\n')][1:-2]

    def _is_mail_id(self, mail_id):
        """
        Check mail_id for a valid postfix queued mail ID.

        Validation is made using a :class:`re.RegexObject` stored in
        the :attr:`~PostqueueStore.mail_id_re` attribute of the
        :class:`~store.PostqueueStore` instance.

        :param str mail_id: Mail Postfix queue ID string
        :return: True or false
        :rtype: :func:`bool`
        """

        if self.mail_id_re.match(mail_id) is None:
            return False
        return True

    @debug
    def _load_from_postqueue(self, filename=None, parse=False):
        """
        Load content from postfix queue using postqueue command output.

        Output lines from :attr:`~store.PostqueueStore._get_postqueue_output`
        are parsed to build :class:`~store.Mail` objects. Sample Postfix queue
        control tool (`postqueue`_) output::

            C0004979687     4769 Tue Apr 29 06:35:05  sender@domain.com
            (error message from mx.remote1.org with parenthesis)
                                                     first.rcpt@remote1.org
            (error message from mx.remote2.org with parenthesis)
                                                     second.rcpt@remote2.org
                                                     third.rcpt@remote2.org

        Parsing rules are pretty simple:

        - Line starts with a valid :attr:`Mail.qid`: create new
          :class:`~store.Mail` object with :attr:`~Mail.qid`,
          :attr:`~Mail.size`, :attr:`~Mail.date` and :attr:`~Mail.sender`
          informations from line.

          +-------------+------+---------------------------+-----------------+
          | Queue ID    | Size | Reception date and time   | Sender          |
          +-------------+------+-----+-----+----+----------+-----------------+
          | C0004979687 | 4769 | Tue | Apr | 29 | 06:35:05 | user@domain.com |
          +-------------+------+-----+-----+----+----------+-----------------+

        - Line starts with a parenthesis: store error messages to last created
          :class:`~store.Mail` object's :attr:`~Mail.errors` attribute.

        - Any other matches: add new recipient to the :attr:`~Mail.recipients`
          attribute of the last created :class:`~store.Mail` object.

        Optionnal argument ``filename`` can be set with a file containing
        output of the `postqueue`_ command. In this case, output lines of
        `postqueue`_ command are directly read from ``filename`` and parsed,
        the `postqueue`_ command is never used.

        Optionnal argument ``parse`` controls whether mails are parsed or not.
        This is useful to load every known mail headers for later filtering.

        :param str filename: File to load mails from
        :param bool parse: Controls whether loaded mails are parsed or not.
        """
        if filename is None:
            postqueue_output = self._get_postqueue_output()
        else:
            postqueue_output = open(filename).readlines()

        mail = None
        for line in postqueue_output:
            line = line.strip()

            # Headers and footers start with dash (-)
            if line.startswith('-'):
                continue
            # Mails are blank line separated
            if not len(line):
                continue

            fields = line.split()
            if "(" == fields[0][0]:
                # Store error message without parenthesis: [1:-1]
                # gathered errors must be associated with specific recipients
                # TODO: change recipients or errors structures to link these
                #       objects together.
                mail.errors.append(" ".join(fields)[1:-1])
            else:
                if self._is_mail_id(fields[0]):
                    # postfix does not precise year in mails timestamps so
                    # we consider mails have been sent this year.
                    # If gathered date is in the future:
                    # mail has been received last year (or NTP problem).
                    now = datetime.now()
                    datestr = "{0} {1}".format(" ".join(fields[2:-1]), now.year)
                    date = datetime.strptime(datestr, "%a %b %d %H:%M:%S %Y")
                    if date > now:
                        date = date - timedelta(days=365)

                    mail = self.MailClass(fields[0], size=fields[1],
                                          date=date,
                                          sender=fields[-1])
                    self.mails.append(mail)
                else:
                    # Email address validity check can be tricky. RFC3696 talks
                    # about. Fow now, we use a simple regular expression to
                    # match most of email addresses.
                    rcpt_email_addr = " ".join(fields)
                    if self.mail_addr_re.match(rcpt_email_addr):
                        mail.recipients.append(rcpt_email_addr)

        if parse:
            print("parsing mails")
            [mail.parse() for mail in self.mails]

    @debug
    def _load_from_spool(self, parse=True):
        """
        Load content from postfix queue using files from spool.

        Mails are loaded using the command defined in
        :attr:`~PostqueueStore.postqueue_cmd` attribute. Some informations may
        be missing using the :meth:`~store.PostqueueStore._load_from_spool`
        method, including at least :attr:`Mail.status` field.

        Optionnal argument ``parse`` controls whether mails are parsed or not.
        This is useful to load every known mail headers for later filtering.

        Loaded mails are stored as :class:`~store.Mail` objects in
        :attr:`~PostqueueStore.mails` attribute.

        :param bool parse: Controls whether loaded mails are parsed or not.

        .. warning::

            Be aware that parsing mails on disk is slow and can lead to
            high load usage on system with large mails queue.
        """
        for status in self.postqueue_mailstatus:
            for fs_data in os.walk("%s/%s" % (self.spool_path, status)):
                for mail_id in fs_data[2]:
                    mail = self.MailClass(mail_id)
                    mail.status = status

                    mail.parse()

                    self.mails.append(mail)

    @debug
    def _load_from_file(self, filename):
        """Unimplemented method"""

    @debug
    def load(self, method="postqueue", filename=None, parse=False):
        """
        Load content from postfix mails queue.

        Mails are loaded using postqueue command line tool or reading directly
        from spool. The optionnal argument, if present, is a method string and
        specifies the method used to gather mails informations. By default,
        method is set to ``"postqueue"`` and the standard Postfix queue
        control tool: `postqueue`_ is used.

        Optionnal argument ``parse`` controls whether mails are parsed or not.
        This is useful to load every known mail headers for later filtering.

        :param str method: Method used to load mails from Postfix queue
        :param str filename: File to load mails from
        :param bool parse: Controls whether loaded mails are parsed or not.

        Provided method :func:`str` name is directly used with :func:`getattr`
        to find a *self._load_from_<method>* method.
        """
        # releasing memory
        del self.mails
        gc.collect()

        self.mails = []
        if filename is None:
            getattr(self, "_load_from_{0}".format(method))(parse=parse)
        else:
            getattr(self, "_load_from_{0}".format(method))(filename, parse)
        self.loaded_at = datetime.now()

    @debug
    def summary(self):
        """
        Summarize the mails queue content.

        :return: Mail queue summary as :class:`dict`

        Sizes are in bytes.

        Example response::

            {
                'total_mails': 500,
                'total_mails_size': 709750,
                'average_mail_size': 1419.5,
                'max_mail_size': 2414,
                'min_mail_size': 423,
                'top_errors': [
                    ('mail transport unavailable', 484),
                    ('Test error message', 16)
                ],
                'top_recipient_domains': [
                    ('test-domain.tld', 500)
                ],
                'top_recipients': [
                    ('user-3@test-domain.tld', 200),
                    ('user-2@test-domain.tld', 200),
                    ('user-1@test-domain.tld', 100)
                ],
                'top_sender_domains': [
                    ('test-domain.tld', 500)
                ],
                'top_senders': [
                    ('sender-1@test-domain.tld', 100),
                    ('sender-2@test-domain.tld', 100),
                    ('sender-7@test-domain.tld', 50),
                    ('sender-4@test-domain.tld', 50),
                    ('sender-5@test-domain.tld', 50)
                ],
                'top_status': [
                    ('deferred', 500),
                    ('active', 0),
                    ('hold', 0)
                ],
                'unique_recipient_domains': 1,
                'unique_recipients': 3,
                'unique_sender_domains': 1,
                'unique_senders': 8
            }
        """
        senders = Counter()
        sender_domains = Counter()
        recipients = Counter()
        recipient_domains = Counter()
        status = Counter(active=0, hold=0, deferred=0)
        errors = Counter()
        total_mails_size = 0
        average_mail_size = 0
        max_mail_size = 0
        min_mail_size = 0
        mails_by_age = {
            'last_24h': 0,
            '1_to_4_days_ago': 0,
            'older_than_4_days': 0
        }

        for mail in self.mails:
            status[mail.status] += 1
            senders[mail.sender] += 1
            if '@' in mail.sender:
                sender_domains[mail.sender.split('@', 1)[1]] += 1
            for recipient in mail.recipients:
                recipients[recipient] += 1
                if '@' in recipient:
                    recipient_domains[recipient.split('@', 1)[1]] += 1
            for error in mail.errors:
                errors[error] += 1
            total_mails_size += mail.size
            if mail.size > max_mail_size:
                max_mail_size = mail.size
            if min_mail_size == 0:
                min_mail_size = mail.size
            elif mail.size < min_mail_size:
                min_mail_size = mail.size

            mail_age = datetime.now() - mail.date
            if mail_age.days >= 4:
                mails_by_age['older_than_4_days'] += 1
            elif mail_age.days == 1:
                mails_by_age['1_to_4_days_ago'] += 1
            elif mail_age.days == 0:
                mails_by_age['last_24h'] += 1

        if len(self.mails):
            average_mail_size = total_mails_size / len(self.mails)

        summary = {
            'total_mails': len(self.mails),
            'mails_by_age': mails_by_age,
            'total_mails_size': total_mails_size,
            'average_mail_size': average_mail_size,
            'max_mail_size': max_mail_size,
            'min_mail_size': min_mail_size,
            'top_status': status.most_common()[:5],
            'unique_senders': len(list(senders)),
            'unique_sender_domains': len(list(sender_domains)),
            'unique_recipients': len(list(recipients)),
            'unique_recipient_domains': len(list(recipient_domains)),
            'top_senders': senders.most_common()[:5],
            'top_sender_domains': sender_domains.most_common()[:5],
            'top_recipients': recipients.most_common()[:5],
            'top_recipient_domains': recipient_domains.most_common()[:5],
            'top_errors': errors.most_common()[:5]
        }
        return summary
