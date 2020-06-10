# TODO: Convert note to reply and use ticket things
# Copyright 2018 Autodesk, Inc.  All rights reserved.
#
# Use of this software is subject to the terms of the Autodesk license agreement
# provided at the time of installation or download, or which otherwise accompanies
# this software in either electronic or hard copy form.
#

import re

from jira import JIRAError
from ..errors import InvalidJiraValue
from ..constants import SHOTGUN_JIRA_ID_FIELD, SHOTGUN_SYNC_IN_JIRA_FIELD
from .sync_handler import SyncHandler

# Template used to build Jira comments body from a Note.
COMMENT_BODY_TEMPLATE = """
{{panel}}
{body}
{{panel}}
"""


class TicketReplyCommentHandler(SyncHandler):
    """
    Sync a Shotgun Ticket Reply with a comment attached to the associated Jira Issue for
    this Task.

    .. note:: The same Shotgun Note can be attached to multiple Tasks, but it is
              not possible to share the same comment across multiple Issues in
              Jira. If a Note is attached to multiple Tasks, only one Issue comment
              will be updated.
    """
    # Define the mapping between Shotgun Note fields and Jira Comment fields.
    # If the Jira target is None, it means the target field is not settable
    # directly.
    __REPLY_FIELDS_MAPPING = {
        "content": None,
        "user": None
    }

    def setup(self):
        """
        Check the Jira and Shotgun site, ensure that the sync can safely happen
        and cache any value which is slow to retrieve.
        """
        self._shotgun.assert_field(
            "Reply",
            SHOTGUN_JIRA_ID_FIELD,
            "text",
            check_unique=True
        )

    def _supported_shotgun_fields_for_shotgun_event(self):
        """
        Return the list of Shotgun fields that this handler can process for a
        Shotgun to Jira event.
        """
        return self.__REPLY_FIELDS_MAPPING.keys()

    def _compose_jira_comment_body(self, shotgun_reply):
        """
        Return a body value to update a Jira comment from the given Shotgun Note.

        :param shotgun_reply: A Shotgun Reply dictionary.
        :returns: A string.
        """
        # TODO: Change this to match desired comment structure
        return COMMENT_BODY_TEMPLATE.format(body=shotgun_reply["content"])

    def _compose_shotgun_reply(self, jira_comment):
        """
        Return a subject and content value to update a Shotgun Note from the
        given Jira comment.

        Notes created in SG are stored in Jira with some fanciness markup (see
        ``COMMENT_BODY_TEMPLATE``) to mimic the subject and content format that SG has.
        This attempts to parse the Jira Comment assuming this format is still
        intact.

        If the subject and content cannot be parsed, we raise an exception
        since we can't reliably determine what the Note should contain.

        Any changes to the template above will require updating this logic.

        :param str jira_comment: A Jira comment body.
        :returns tuple: a tuple containing the subject and content as strings.
        :raises InvalidJiraError: if the Jira Comment body is not in the
            expected format as defined by ``COMMENT_BODY_TEMPLATE``.
        """
        # TODO: May not need this for Reply. If needed, will need changes anyway.
        result = re.search(
            r"\{panel:[a-zA-Z0-9=#]*\}(.*)\{panel\}",
            jira_comment,
            flags=re.S
        )
        # We can't reliably determine what the Note should contain
        if not result:
            raise InvalidJiraValue(
                "content",
                jira_comment,
                "Invalid Jira Comment body format. Unable to parse Shotgun "
                "content from '%s'" % jira_comment
            )
        content = result.group(1).strip()

        return content

    def _get_jira_issue_comment(self, jira_issue_key, jira_comment_id):
        """
        Retrieve the Jira comment with the given id attached to the given Issue.

        .. note:: Jira comments can't live without being attached to an Issue,
                  so we use a "<Issue key>/<Comment id>" key to reference a
                  particular comment.

        :param str jira_issue_key: A Jira Issue key.
        :param str jira_comment_id: A Jira Comment id.
        :returns: A :class:`jira.Comment` instance or None.
        """
        jira_comment = None
        try:
            jira_comment = self._jira.comment(jira_issue_key, jira_comment_id)
        except JIRAError as e:
            # Jira raises a 404 error if it can't find the Comment: catch the
            # error and keep the None value
            if e.status_code == 404:
                pass
            else:
                raise
        return jira_comment

    def accept_shotgun_event(self, entity_type, entity_id, event):
        """
        Accept or reject the given event for the given Shotgun Entity.

        :returns: `True if the event is accepted for processing, `False` otherwise.
        """
        # Note: we don't accept events for the SHOTGUN_SYNC_IN_JIRA_FIELD field
        # but we process them. Accepting the event is done by a higher level handler.
        # Events are accepted by a single handler, which is safer than letting
        # multiple handlers accept the same event: this allows the logic of processing
        # to be easily controllable and understandable.
        # However, there are cases where we want to re-use the processing logic.
        # For example, when the Sync In Jira checkbox is turned on, we want to
        # sync the task, and then its notes.
        # This processing logic is already available in the `TaskIssueHandler`
        # and the `NoteCommentHandler`. So the `EnableSyncingHandler` accepts
        # the event, and then calls `TaskIssueHandler.process_shotgun_event` and,
        # only if this was successful, `NoteCommentHandler.process_shotgun_event`.

        if entity_type != "Reply":
            return False
        meta = event["meta"]
        field = meta["attribute_name"]
        if field not in self._supported_shotgun_fields_for_shotgun_event():
            self._logger.debug(
                "Rejecting Shotgun event for unsupported Shotgun field %s: %s" % (
                    field, event
                )
            )
            return False

        return True

    @property
    def _shotgun_reply_fields(self):
        return ["created_by",
                "created_at",
                "project",
                "project.Project." + SHOTGUN_JIRA_ID_FIELD,
                "project.Project.name",
                "content",
                "user",
                "entity",
                SHOTGUN_JIRA_ID_FIELD]

    def _parse_reply_jira_key(self, sg_reply):
        """
        Parse the Jira key value set in the given Shotgun Reply and return the Jira
        Issue key and the Jira comment id it refers to, if it is not empty.

        :returns: A tuple with a Jira Issue key and a Jira comment id, or
                  `None, None`.
        :raises ValueError: if the Jira key is invalid.
        """
        if not sg_reply[SHOTGUN_JIRA_ID_FIELD]:
            return None, None
        parts = sg_reply[SHOTGUN_JIRA_ID_FIELD].split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                "Invalid Jira comment id %s, it must be in the format "
                "'<jira issue key>/<jira comment id>'" % (
                    sg_reply[SHOTGUN_JIRA_ID_FIELD]
                )
            )
        return parts[0], parts[1]

    def process_shotgun_event(self, entity_type, entity_id, event):
        """
        Process the given Shotgun event for the given Shotgun Entity

        :param str entity_type: The Shotgun Entity type to sync.
        :param int entity_id: The id of the Shotgun Entity to sync.
        :param event: A dictionary with the event for the change.
        :returns: True if the event was successfully processed, False if the
                  sync didn't happen for any reason.
        """
        meta = event["meta"]
        shotgun_field = meta["attribute_name"]

        # NOTE: We're don't validate that a Comment is configured to sync with
        # the source Note that initiated the sync because Jira Comments don't
        # store any linked Shotgun Entity info like Issues do.

        # Note: we don't accept events for the SHOTGUN_SYNC_IN_JIRA_FIELD field
        # but we process them.
        # Accepting the event is done by a higher level handler.
        if shotgun_field == SHOTGUN_SYNC_IN_JIRA_FIELD:
            # Note: in this case the Entity is a Ticket.
            return self._sync_shotgun_ticket_replies_to_jira(
                {"type": entity_type, "id": entity_id})

        sg_reply = self._shotgun.consolidate_entity(
            {"type": entity_type, "id": entity_id},
            fields=self._shotgun_reply_fields)
        if not sg_reply:
            self._logger.warning(
                "Unable to find Shotgun %s (%s)" % (
                    entity_type,
                    entity_id)
            )
            return False
        # Ignore replies that are not Ticket related
        if sg_reply["entity"]["type"] != "Ticket":
            return False

        # When an Entity is created in Shotgun, a unique event is generated for
        # each field value set in the creation of the Entity. These events
        # have an additional "in_create" key in the metadata, identifying them
        # as events from the initial create event.
        #
        # When the bridge processes the first event, it loads all of the Entity
        # field values from Shotgun and creates the Jira Issue with those
        # values. So the remaining Shotgun events with the "in_create"
        # metadata key can be ignored since we've already handled all of
        # those field updates.

        # We use the Jira id field value to check if we're processing the first
        # event. If it exists with in_create, we know the comment has already
        # been created.
        if sg_reply[SHOTGUN_JIRA_ID_FIELD] and meta.get("in_create"):
            self._logger.debug(
                "Rejecting Shotgun event for Note.%s field update during "
                "create. Comment was already created in Jira: %s" % (
                    shotgun_field, event)
            )
            return False
        if sg_reply[SHOTGUN_JIRA_ID_FIELD]:
            self._logger.debug(
                "Shotgun Note (%d).%s updated" % (
                    sg_reply["id"],
                    shotgun_field)
            )
            # Update the Jira comment body
            return self._update_reply_content_to_jira(sg_reply)
        else:
            return self._create_reply_on_jira(sg_reply)

    def _update_reply_content_to_jira(self, sg_reply):
        """
        Update an existing Jira Comment body from the Shotgun Reply fields.

        :param sg_reply: A Shotgun Note dictionary.
        :returns: `True` if a Jira Comment was updated, `False` otherwise.
        """
        jira_issue_key, jira_comment_id = self._parse_reply_jira_key(sg_reply)
        if jira_issue_key and jira_comment_id:
            # Double check that there is a valid Ticket linked to this Reply and the
            # Jira Issue.
            if not sg_reply["entity"] or not self._shotgun.find_one(
                    "Ticket", [
                        ["id", "is", sg_reply["entity"]["id"]],
                        [SHOTGUN_JIRA_ID_FIELD, "is", jira_issue_key],
                        [SHOTGUN_SYNC_IN_JIRA_FIELD, "is", True]
                    ]):
                self._logger.debug(
                    "Not updating Jira Issue %s comment %s from Shotgun Note %s."
                    "Note is not linked to a synced Task that currently has "
                    "syncing enabled" % (
                        jira_issue_key,
                        jira_comment_id,
                        sg_reply)
                )
                return False

            jira_comment = self._get_jira_issue_comment(jira_issue_key,
                                                        jira_comment_id)
            if jira_comment:
                self._logger.info(
                    "Shotgun Note (%d) updated. Syncing to Jira Issue %s Comment %s" % (
                        sg_reply["id"],
                        jira_issue_key,
                        jira_comment))
                jira_comment.update(body=self._compose_jira_comment_body(sg_reply))
                return True
        return False

    def _create_reply_on_jira(self, sg_reply):
        """
        Create a new Jira Comment from the Shotgun Reply.

        :param sg_reply: A Shotgun Reply dictionary.
        :returns: `True` if a Jira Comment was created, `False` otherwise.
        """
        if sg_reply["entity"]["type"] != "Ticket":
            return False
        sg_ticket = self._shotgun.find_one("Ticket",
                                           [["id", "is", sg_reply["entity"]["id"]]],
                                           fields=[SHOTGUN_JIRA_ID_FIELD,
                                                   SHOTGUN_SYNC_IN_JIRA_FIELD])
        jira_issue = self.get_jira_issue(sg_ticket[SHOTGUN_JIRA_ID_FIELD])
        if not jira_issue:
            self._logger.warning(
                "Unable to find Jira Issue %s for Reply %s" % (sg_ticket[SHOTGUN_JIRA_ID_FIELD],
                                                               sg_reply))
            return False
        self._logger.info(
            "Shotgun Reply (%d) added. Adding as a new comment on Jira Issue %s" % (
                sg_reply["id"],
                jira_issue.key))
        jira_comment = self._jira.add_comment(jira_issue,
                                              self._compose_jira_comment_body(sg_reply),
                                              visibility=None,
                                              is_internal=False)
        jira_issue_key = jira_issue.key
        jira_comment_id = jira_comment.id

        # Update the Jira comment key in Shotgun
        comment_key = None
        if jira_issue_key and jira_comment_id:
            comment_key = "%s/%s" % (jira_issue_key, jira_comment_id)
        if comment_key != sg_reply[SHOTGUN_JIRA_ID_FIELD]:
            self._logger.info(
                "Updating Shotgun Reply (%d) with Jira comment key %s" % (
                    sg_reply["id"],
                    comment_key,
                )
            )
            self._shotgun.update(
                sg_reply["type"],
                sg_reply["id"],
                {SHOTGUN_JIRA_ID_FIELD: comment_key}
            )

    def accept_jira_event(self, resource_type, resource_id, event):
        """
        Accept or reject the given event for the given Jira resource.

        .. note:: The event for Comments is different than a standard Issue
                  event. There is no ``changelog`` key. The ``issue`` value
                  doesn't contain the full schema, just the basic fields. So
                  the logic in here and in :method:`process_jira_event`, is
                  a little different than in Issue-based handlers. For
                  example, we can't examine the existing Issue fields to see
                  whether the issue is synced with Shotgun without doing
                  another query somewhere, so we leave this to
                  :method:`process_jira_event`.

        :param str resource_type: The type of Jira resource sync, e.g. Issue.
        :param str resource_id: The id of the Jira resource to sync.
        :param event: A dictionary with the event meta data for the change.
        :returns: True if the event is accepted for processing, False otherwise.
        """
        if resource_type.lower() != "issue":
            self._logger.debug(
                "Rejecting event for a %s Jira resource. Handler only "
                "accepts Issue resources." % resource_type
            )
            return False
        # Check the event payload and reject the event if we don't have what we
        # expect
        jira_issue = event.get("issue")
        if not jira_issue:
            self._logger.debug("Rejecting event without an issue: %s" % event)
            return False

        jira_comment = event.get("comment")
        if not jira_comment:
            self._logger.debug("Rejecting event without a comment: %s" % event)
            return False

        webhook_event = event.get("webhookEvent")
        if not webhook_event:
            self._logger.debug("Rejecting event without a webhookEvent: %s" % event)
            return False

        if webhook_event == "comment_deleted":
            self._logger.warning(
                "Not handling 'comment_deleted' event. Event data: %s" % (event)
            )
            return False

        return True

    def process_jira_event(self, resource_type, resource_id, event):
        """
        Process the given Jira event for the given Jira resource.

        :param str resource_type: The type of Jira resource to sync, e.g. Issue.
        :param str resource_id: The id of the Jira resource to sync.
        :param event: A dictionary with the event meta data for the change.
        :returns: True if the event was successfully processed, False if the
                  sync didn't happen for any reason.
        """
        jira_issue = event["issue"]
        jira_comment = event["comment"]
        webhook_event = event["webhookEvent"]

        # construct our Jira key for Notes and check if we have an existing
        # Shotgun Note to update.
        # key <jira issue key>/<jira comment id>.
        sg_jira_key = "%s/%s" % (jira_issue["key"], jira_comment["id"])
        sg_replies = self._shotgun.find(
            "Reply",
            [[SHOTGUN_JIRA_ID_FIELD, "is", sg_jira_key]],
            fields=["content", "entity"]
        )

        # If we have more than one Note with the same key, we don't want to
        # create more mess.
        if len(sg_replies) > 1:
            self._logger.warning(
                "Unable to process Jira Comment %s event. More than one Reply "
                "exists in Shotgun with Jira key %s: %s" % (
                    webhook_event,
                    sg_jira_key,
                    sg_replies)
            )
            return False
        sg_data = {}
        try:
            sg_data["content"] = self._compose_shotgun_reply(jira_comment["body"])
        except InvalidJiraValue as e:
            msg = "Unable to process Jira Comment %s event. %s" % (
                webhook_event,
                e)
            self._logger.debug(msg, exc_info=True)
            self._logger.warning(msg)
            return False
        if len(sg_replies) == 1:  # Update existing Reply
            # TODO: Check that the Ticket the Reply is linked to has syncing enabled.
            #       Otherwise syncing could be turned off for the Task but this
            #       will still sync the Note.
            self._logger.info(
                "Jira %s %s Comment %s updated. Syncing to Shotgun Reply (%d)" % (
                    resource_type,
                    resource_id,
                    jira_comment["id"],
                    sg_replies[0]["id"])
            )
            self._logger.debug("Jira event: %s" % event)
            self._logger.debug(
                "Updating Shotgun Note %d (jira_key:%s) with data: %s" % (
                    sg_replies[0]["id"],
                    sg_jira_key,
                    sg_data)
            )

            self._shotgun.update(
                "Note",
                sg_replies[0]["id"],
                sg_data)
            return True
        if not sg_replies:
            self._logger.warning("No existing reply found. New reply should be made but currently "
                                 "not implemented.")
            ticket = self._shotgun.find_one("Ticket",
                                            [[SHOTGUN_JIRA_ID_FIELD, "is", jira_issue["key"]]])
            if ticket is None:
                self._logger.warning(
                    "Could not find a Ticket associatied with {}. No reply is created."
                    "".format(jira_issue["key"])
                )
                return False
            sg_data.update(
                {SHOTGUN_JIRA_ID_FIELD: sg_jira_key,
                 "entity": ticket,
                 "content": self._compose_shotgun_reply(jira_comment["body"]),
                 # "user": None,
                 })
            self._shotgun.create(
                "Reply",
                sg_data
            )

    def _sync_shotgun_ticket_replies_to_jira(self, sg_ticket):
        """
        Sync all Replies attached to the given Shotgun Ticket to Jira.

        :param sg_ticket: A Shotgun Ticket dictionary.
        :returns: `True` if any update happened, `False` otherwise.
        """
        sg_replies = self._shotgun.find(
            "Reply",
            [["entity", "is", sg_ticket]],
            self._shotgun_reply_fields)
        self._logger.debug("Retrieved Notes %s linked to Task %s" % (sg_replies, sg_ticket))
        updated = False
        for sg_reply in sorted(sg_replies, key=lambda r: r["created_at"]):
            if not sg_reply[SHOTGUN_JIRA_ID_FIELD]:
                self._create_reply_on_jira(sg_reply)
            else:
                self._update_reply_content_to_jira(sg_reply)
        return updated
