# Author: Zhang Huangbin <zhb _at_ iredmail.org>

import sys
import ldap
import logging
import settings
from libs import SMTP_ACTIONS, utils
from libs.ldaplib import conn_utils
from libs.amavisd import core as amavisd_lib
from libs.log_to_db import log_action


class Modeler:
    def __init__(self):
        # Initialize ldap connection.
        try:
            self.conn = ldap.initialize(settings.ldap_uri)
            logging.debug('LDAP connection initialied success.')
        except Exception, e:
            logging.error('LDAP initialized failed: %s.' % str(e))
            sys.exit()

        # Bind to ldap server.
        try:
            self.conn.bind_s(settings.ldap_binddn, settings.ldap_bindpw)
            logging.debug('LDAP bind success.')
        except ldap.INVALID_CREDENTIALS:
            logging.error('LDAP bind failed: incorrect bind dn or password.')
            sys.exit()
        except Exception, e:
            logging.error('LDAP bind failed: %s.' % str(e))
            sys.exit()

    def __del__(self):
        try:
            self.conn.unbind_s()
            logging.debug('Close LDAP connection.')
        except Exception, e:
            logging.debug('Error while closing connection: %s' % str(e))

    def handle_data(self,
                    smtp_session_data,
                    plugins=[],
                    sender_search_attrlist=[],
                    recipient_search_attrlist=[]):
        # No sender or recipient in smtp session.
        if not 'sender' in smtp_session_data or \
                not 'recipient' in smtp_session_data:
            return SMTP_ACTIONS['default']

        # No plugins available.
        if not plugins:
            return 'DUNNO'

        sender = smtp_session_data['sender'].lower()
        recipient = smtp_session_data['recipient'].lower()
        sasl_username = smtp_session_data['sasl_username'].lower()
        smtp_protocol_state = smtp_session_data['protocol_state'].upper()

        plugin_kwargs = {'smtp_session_data': smtp_session_data,
                         'conn': self.conn,
                         'base_dn': settings.ldap_basedn,
                         'sender': sender,
                         'sender_domain': sender.split('@', 1)[-1],
                         'recipient': recipient,
                         'recipient_domain': recipient.split('@', 1)[-1],
                         'sasl_username': sasl_username,
                         'sender_dn': None,
                         'sender_ldif': None,
                         'recipient_dn': None,
                         'recipient_ldif': None,
                         'amavisd_db_cursor': None}

        # TODO Perform addition plugins which don't require sender/recipient info
        # e.g.
        #   - security enforce: encryption_protocol=TLSv1/SSLv3

        for plugin in plugins:
            # Get plugin target smtp protocol state
            try:
                target_smtp_protocol_state = plugin.SMTP_PROTOCOL_STATE
            except:
                target_smtp_protocol_state = 'RCPT'

            if smtp_protocol_state != target_smtp_protocol_state:
                logging.debug('Skip plugin: %s (protocol_state != %s)' % (plugin.__name__, smtp_protocol_state))
                continue

            # Get LDIF data of sender if required
            try:
                require_local_sender = plugin.REQUIRE_LOCAL_SENDER
            except:
                require_local_sender = False

            if require_local_sender and plugin_kwargs['sender_dn'] is None:
                sender_dn, sender_ldif = conn_utils.get_account_ldif(
                    conn=self.conn,
                    account=sasl_username,
                    attrs=sender_search_attrlist,
                )
                plugin_kwargs['sender_dn'] = sender_dn
                plugin_kwargs['sender_ldif'] = sender_ldif

            # Get LDIF data of recipient if required
            try:
                require_local_recipient = plugin.REQUIRE_LOCAL_RECIPIENT
            except:
                require_local_recipient = False

            if require_local_recipient and plugin_kwargs['recipient_dn'] is None:
                recipient_dn, recipient_ldif = conn_utils.get_account_ldif(
                    conn=self.conn,
                    account=recipient,
                    attrs=recipient_search_attrlist,
                )
                plugin_kwargs['recipient_dn'] = recipient_dn
                plugin_kwargs['recipient_ldif'] = recipient_ldif

            # Connect to Amavisd database if required
            try:
                plugin_require_amavisd_db = plugin.REQUIRE_AMAVISD_DB
            except:
                plugin_require_amavisd_db = False

            if plugin_require_amavisd_db:
                if not plugin_kwargs['amavisd_db_cursor']:
                    try:
                        amavisd_db_wrap = amavisd_lib.AmavisdDBWrap()
                        plugin_kwargs['amavisd_db_cursor'] = amavisd_db_wrap.cursor
                        logging.debug('Got db cursor.')
                    except Exception, e:
                        logging.debug('Skip plugin, error while getting db cursor: %s' % str(e))
                        continue

            # Apply plugins
            action = utils.apply_plugin(plugin, **plugin_kwargs)
            if not action.startswith('DUNNO'):
                # Log action
                log_action(action=action,
                           sender=sender,
                           recipient=recipient,
                           ip=smtp_session_data['client_address'],
                           plugin_name=plugin.__name__)

                return action

        return SMTP_ACTIONS['default']
