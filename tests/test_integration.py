# -*- coding: utf-8 -*-

# Copyright 2018 Autodesk, Inc.  All rights reserved.
#
# Use of this software is subject to the terms of the Autodesk license agreement
# provided at the time of installation or download, or which otherwise accompanies
# this software in either electronic or hard copy form.
#

from __future__ import print_function
import os
import time
import threading

from shotgun_api3 import Shotgun
from jira import JIRA

from unittest2 import TestCase, skipIf

import webapp


# Inspired by https://docs.python.org/2/library/basehttpserver.html#more-examples
class ServerThread(threading.Thread):
    """
    Thread that spawns the jira bridge server.

    When stop is invoked, the bridge is closed.
    """

    def __init__(self):
        """
        Init.
        """
        super(ServerThread, self).__init__()
        self._httpd = webapp.create_server(
            9090, os.path.join(os.path.dirname(__file__), "..", "settings.py")
        )

    def run(self):
        """
        Handles requests until the server is closed.
        """
        try:
            self._httpd.serve_forever()
        except Exception:
            # Simply swallow the error that will be raised here because of the
            # socket closure.
            pass

    def stop(self):
        """
        Stop the server.
        """
        # We're closing the socket violently, since handle_request is a blocking
        # call.
        try:
            self._httpd.socket.close()
        except Exception:
            pass


missing_env_vars = [
    env_var
    for env_var in [
        "SGJIRA_SG_SITE",
        "SGJIRA_SG_TEST_PASSWORD",
        "SGJIRA_SG_TEST_PROJECT",
        "SGJIRA_SG_TEST_USER",
        "SGJIRA_SG_TEST_USER_2",
        "SGJIRA_JIRA_SITE",
        "SGJIRA_JIRA_TEST_USER",
        "SGJIRA_JIRA_TEST_USER_SECRET",
        "SGJIRA_JIRA_TEST_PROJECT_KEY",
        "SGJIRA_JIRA_TEST_USER_2",
    ]
    if env_var not in os.environ
]


@skipIf(len(missing_env_vars) > 0, "missing {}".format(", ".join(missing_env_vars)))
class TestIntegration(TestCase):
    """
    Integration test for the bridge. This test uses actual
    JIRA and Shotgun servers.

    This test expects a project to have been configured in JIRA and Shotgun
    according to the Quickstart page of the documentation, including the webhook
    configuration and having run the update_shotgun_users.py script if this
    test is being run against the JIRA site hosted by Atlassian.

    In addition to the JIRA user used to sync data to JIRA, two other users
    need to be created in JIRA with an email that matches two other users in
    Shotgun. DO NOT reuse the email used by the JIRA bridge script or this
    test will fail since the bridge ignores webhook events generated by that
    user.

    To run this test, the following environment variables need to be set:

    For Shotgun access:
    - SGJIRA_SG_SITE: The Shotgun site to use.
    - SGJIRA_SG_TEST_USER: The login of the first user for the test.
    - SGJIRA_SG_TEST_PASSWORD: The password for that user. Required so
        we can authenticate to Shotgun.
    - SGJIRA_SG_TEST_PROJECT: The test project for which we should sync data.
    - SGJIRA_SG_TEST_USER_2: The login of the second user.

    For JIRA access:
    - SGJIRA_JIRA_SITE: The JIRA site to use.
    - SGJIRA_JIRA_TEST_USER: The login of the user that matches the first
        Shotgun test user.
    - SGJIRA_JIRA_TEST_USER_SECRET: The secret of the user used to authenticate
        with JIRA.
    - SGJIRA_JIRA_TEST_USER_2: The login (Jira Server) or accountId (Jira Cloud) of the user that matches the second
        Shotgun test user.
    - SGJIRA_JIRA_TEST_PROJECT_KEY: The Jira project for which we should sync data.

    The test will take care of starting and stopping the JIRA integration webapp,
    but you are responsible for running your own Shotgun Event daemon.

    If you already have a JIRA site up and running, whether it's on the cloud
    or on-premise, you're good to go. However, you might want to run this
    test against specific versions of JIRA or on a test server.

    Atlassian provides docker images. You can find the documentation about them
    here: https://hub.docker.com/r/atlassian/jira-software.

    It can be as simple as:
    docker volume create --name jira_7_13_volume
    docker run -v jira_7_13_volume:/var/atlassian/application-data/jira --name="jira_7_13" -d -p 8080:8080 atlassian/jira-software:7.13

    The server can then easily be stopped and started via `docker start/stop jira_7_13`.

    Once you connect to the server on localhost:8080, you can request a trial license
    or paste your own license.
    """

    @classmethod
    def setUpClass(cls):
        """
        Connect to both servers and resolve all users.
        """
        # Connect to Shotgun
        cls._sg = Shotgun(
            os.environ["SGJIRA_SG_SITE"],
            login=os.environ["SGJIRA_SG_TEST_USER"],
            password=os.environ["SGJIRA_SG_TEST_PASSWORD"],
        )

        # Resolve the project by name
        cls._sg_project = cls._sg.find_one(
            "Project", [["name", "is", os.environ["SGJIRA_SG_TEST_PROJECT"]]]
        )
        assert cls._sg_project is not None

        # Resolve first Shotgun user
        cls._sg_user_1 = cls._sg.find_one(
            "HumanUser", [["login", "is", os.environ["SGJIRA_SG_TEST_USER"]]]
        )
        assert cls._sg_user_1 is not None

        # Resolve second Shotgun user.
        cls._sg_user_2 = cls._sg.find_one(
            "HumanUser", [["login", "is", os.environ["SGJIRA_SG_TEST_USER_2"]]]
        )
        assert cls._sg_user_2 is not None

        # Connect to JIRA.
        cls._jira = JIRA(
            os.environ["SGJIRA_JIRA_SITE"],
            basic_auth=(
                os.environ["SGJIRA_JIRA_TEST_USER"],
                os.environ["SGJIRA_JIRA_TEST_USER_SECRET"],
            ),
        )

        cls._user_id_field = "accountId" if "accountId" in cls._jira.myself() else "key"

        # Resolve JIRA Project key.
        cls._jira_project = os.environ["SGJIRA_JIRA_TEST_PROJECT_KEY"]
        assert cls._jira_project is not None

        # Resolve first JIRA user key.
        cls._jira_user_1_id = cls._jira.myself()[cls._user_id_field]
        cls._jira_user_2_id = cls._jira.user(
            os.environ["SGJIRA_JIRA_TEST_USER_2"]
        ).user_id

    def _expect(self, functor, description=None, max_time=20):
        """
        Execute a functor until it stops raising errors unless the maximum time is reached.

        :param callable functor: Method to call.
        :param str description: String to print during retries.
        :param int max_time: Number of seconds to wait before giving up.
        """
        before = time.time()

        try:
            return functor()
        except Exception:
            pass

        nb_tries = 2
        while True:
            time.sleep(1)
            print(
                "Retrying '{0}'({1})...".format(
                    description or functor.__name__, nb_tries
                )
            )
            try:
                return functor()
            except Exception:
                if (before + max_time) > time.time():
                    nb_tries += 1
                else:
                    raise

    def _get_jira_key(self, entity):
        """
        Wait until the JIRA key is available on the given entity.

        :param dict entity: Shotgun entity dict with keys type and id.

        :returns: The jira key for that project.
        """

        def wait_for_jira_key():
            result = self._sg.find_one(
                entity["type"], [["id", "is", entity["id"]]], ["sg_jira_key"]
            )
            self.assertIsNotNone(result["sg_jira_key"])
            return result["sg_jira_key"]

        return self._expect(wait_for_jira_key)

    def _create_task(self, name):
        """
        Create a task in Shotgun.

        :param str name: Name of the new task.

        :returns: A tuple of (new task entity dict, JIRA project key).
        """
        new_sg_task = self._sg.create(
            "Task",
            {"content": name, "sg_sync_in_jira": True, "project": self._sg_project},
        )
        jira_key = self._get_jira_key(new_sg_task)
        return new_sg_task, jira_key

    def test_integration(self):
        """
        Test the integration.
        """
        self._server = ServerThread()
        # Ideally the thread start/top would be done in setUp/tearDown, but when hitting
        # CTRL-C to end the tests the tearDown handlers are not invoked. This would
        # leave the process handing because there is still a thread running.
        try:
            self._server.start()
            # Each of these tests could be run in their own test, but the it would
            # take much longer to run because you'd have to create a task for each.
            self._test_create_task()
            self._test_update_status_from_shotgun()
            self._test_update_status_from_jira()
            self._test_update_assignment_from_shotgun()
            self._test_update_assignment_from_jira()
            self._test_update_ccs_from_shotgun()
            self._test_update_description_from_shotgun()
            self._test_update_description_from_jira()
            # TODO: Watchers updates are not pushed to the webhook for some reason,
            # so we can't test them. The test was already written, so we'll keep it.
            # self._test_update_ccs_from_jira()
        finally:
            self._server.stop()

    def _test_create_task(self):
        """
        Ensure a task can be created.
        """
        # Create a task and make sure it gets synced across
        self._sg_task, self._jira_key = self._create_task("Test")
        print(
            "Test Issue can be found at {0}/browse/{1}".format(
                os.environ["SGJIRA_JIRA_SITE"], self._jira_key
            )
        )

    @property
    def _issue(self):
        """
        Resolve the issue associated with the created task.
        """
        return self._jira.issue(self._jira_key)

    def _test_update_status_from_shotgun(self):
        """
        Ensure updating status in Shotgun is reflected in JIRA.
        """

        def wait_for_issue_in_progress():
            self.assertEqual(self._issue.fields.status.name, "In Progress")

        self._sg.update("Task", self._sg_task["id"], {"sg_status_list": "ip"})
        self._expect(wait_for_issue_in_progress)

    def _set_jira_status(self, issue, status_name):
        """
        Update an issue status to the specified one.

        :param issue: sg_jira.resources.Issue to update.
        :param status_name: Name of the status to switch to.
        """
        jira_transitions = self._jira.transitions(issue, expand="transitions.fields")
        for tra in jira_transitions:
            # Match a transition with the expected status name
            if tra["to"]["name"] == status_name:
                break
        else:
            raise RuntimeError("No transitions found for {0}!", status_name)

        self._jira.transition_issue(self._issue, tra["id"])

    def _test_update_status_from_jira(self):
        """
        Ensure updating status in JIRA is reflected in Shotgun.
        """

        def wait_for_shotgun_status_final():
            task = self._sg.find_one(
                "Task", [["id", "is", self._sg_task["id"]]], ["sg_status_list"]
            )
            self.assertEqual(task["sg_status_list"], "fin")

        self._set_jira_status(self._issue, "Done")
        self._expect(wait_for_shotgun_status_final)

    def _test_update_assignment_from_shotgun(self):
        """
        Ensure updating the task_assignees in Shotgun updates the assignee
        in JIRA.
        """

        def wait_for_assignee_to_change(expected_user_id):
            if expected_user_id is None:
                self.assertIsNone(self._issue.fields.assignee)
            else:
                self.assertEqual(
                    getattr(self._issue.fields.assignee, self._user_id_field),
                    expected_user_id,
                )

        # Assign the ticket to a user in Shotgun
        self._sg.update(
            "Task", self._sg_task["id"], {"task_assignees": [self._sg_user_1]}
        )

        # Make sure
        self._expect(
            lambda: wait_for_assignee_to_change(self._jira_user_1_id),
            "waiting_for_jira_user_1_on_issue",
        )

        self._sg.update(
            "Task", self._sg_task["id"], {"task_assignees": [self._sg_user_2]}
        )
        self._expect(
            lambda: wait_for_assignee_to_change(self._jira_user_2_id),
            "waiting_for_jira_user_2_on_issue",
        )

        self._sg.update("Task", self._sg_task["id"], {"task_assignees": []})
        self._expect(
            lambda: wait_for_assignee_to_change(None),
            "waiting_for_cleared_assignment_on_issue",
        )

    def _test_update_assignment_from_jira(self):
        """
        Ensure updating the assignee in Jira updates the assignee
        in Shotgun.
        """

        def wait_for_assignee_to_change(expected_user_ids):
            asssignees = self._sg.find_one(
                "Task", [["id", "is", self._sg_task["id"]]], ["task_assignees"]
            )["task_assignees"]
            self.assertEqual(
                {a["id"] for a in asssignees}, {u["id"] for u in expected_user_ids}
            )

        self._jira.assign_issue(self._jira_key, self._jira_user_1_id)

        self._expect(
            lambda: wait_for_assignee_to_change([self._sg_user_1]),
            "waiting_for_sg_user_1_on_task",
        )

        self._jira.assign_issue(self._jira_key, self._jira_user_2_id)

        self._expect(
            lambda: wait_for_assignee_to_change([self._sg_user_2]),
            "waiting_for_sg_user_2_on_task",
        )

    def _test_update_ccs_from_shotgun(self):
        """
        Ensure updating addressings_cc in Shotgun updates watchers in JIRA.
        """

        def wait_for_watchers_to_be_assigned(expected_users):
            uid_field = self._user_id_field
            server_account = self._server._httpd._sg_jira.jira.myself()[uid_field]
            self.assertEqual(
                # Skip the daemon
                {
                    getattr(w, uid_field)
                    for w in self._jira.watchers(self._jira_key).watchers
                    if getattr(w, uid_field) != server_account
                },
                set(expected_users),
            )

        self._sg.update(
            "Task", self._sg_task["id"], {"addressings_cc": [self._sg_user_1]}
        )
        self._expect(
            lambda: wait_for_watchers_to_be_assigned([self._jira_user_1_id]),
            "waiting_for_jira_1_to_be_watching",
        )

        self._sg.update(
            "Task", self._sg_task["id"], {"addressings_cc": [self._sg_user_2]}
        )
        self._expect(
            lambda: wait_for_watchers_to_be_assigned([self._jira_user_2_id]),
            "waiting_for_jira_2_to_be_watching",
        )

        self._sg.update(
            "Task",
            self._sg_task["id"],
            {"addressings_cc": [self._sg_user_1, self._sg_user_2]},
        )
        self._expect(
            lambda: wait_for_watchers_to_be_assigned(
                [self._jira_user_2_id, self._jira_user_1_id]
            ),
            "waiting_for_jira_1_and_2_to_be_watching",
        )

        self._sg.update(
            "Task", self._sg_task["id"], {"addressings_cc": [self._sg_user_1]}
        )
        self._expect(
            lambda: wait_for_watchers_to_be_assigned([self._jira_user_1_id]),
            "waiting_for_jira_1_to_be_watching",
        )

        self._sg.update("Task", self._sg_task["id"], {"addressings_cc": []})
        self._expect(
            lambda: wait_for_watchers_to_be_assigned([]),
            "waiting_for_no_one_to_be_watching",
        )

    def _test_update_ccs_from_jira(self):
        """
        Ensure updating watchers in JIRA updates the addressing CCs in Shotgun.

        Note that this functionaly has not been written yet, but the test was written
        before we noticed, so we'll keep it around for when the functionality is added.
        """

        def wait_for_addressings_update(expected_users):
            def key_fn(entity):
                return entity["id"]

            self.assertEqual(
                sorted(
                    self._sg.find_one(
                        "Task", [["id", "is", self._sg_task["id"]]], ["addressings_cc"]
                    )["addressings_cc"],
                    key=key_fn,
                ),
                sorted(expected_users, key=key_fn),
            )

        self._jira.add_watcher(self._jira_key, self._jira_user_1_id)
        self._expect(
            lambda: wait_for_addressings_update([self._sg_user_1]),
            "waiting_for_user_1_cced",
        )

        self._jira.add_watcher(self._jira_key, self._jira_user_2_id)
        self._expect(
            lambda: wait_for_addressings_update([self._sg_user_1, self._sg_user_2]),
            "waiting_for_user_1_and_2_cced",
        )

        self._jira.remove_watcher(self._jira_key, self._jira_user_2_id)
        self._expect(
            lambda: wait_for_addressings_update([self._sg_user_1]),
            "waiting_for_user_1_cced",
        )

        self._jira.remove_watcher(self._jira_key, self._jira_user_1_id)
        self._expect(lambda: wait_for_addressings_update([]), "waiting_for_no_one_cced")

    def _test_update_description_from_shotgun(self):
        """
        Ensure updating description in Shotgun is reflected in JIRA.
        """

        def wait_for_shotgun_description_updated():
            self.assertEqual(
                self._issue.fields.description, "Description updated in Shotgun"
            )

        self._sg.update(
            "Task",
            self._sg_task["id"],
            {"sg_description": "Description updated in Shotgun"},
        )
        self._expect(wait_for_shotgun_description_updated)

    def _test_update_description_from_jira(self):
        """
        Ensure updating description in JIRA is reflected in Shotgun.
        """

        def wait_for_jira_description_updated():
            task = self._sg.find_one(
                "Task", [["id", "is", self._sg_task["id"]]], ["sg_description"]
            )
            self.assertEqual(task["sg_description"], "Description updated in Jira")

        self._issue.update(description="Description updated in Jira")
        self._expect(wait_for_jira_description_updated)
