"""Tests for check_flexlm_server, with a fake lmutil replaying recorded lmstat output."""
import datetime
import importlib.machinery
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "check_flexlm_server")
FIXTURES = os.path.join(HERE, "fixtures")

loader = importlib.machinery.SourceFileLoader("check_flexlm_server", PLUGIN)
spec = importlib.util.spec_from_loader("check_flexlm_server", loader)
check_flexlm_server = importlib.util.module_from_spec(spec)
loader.exec_module(check_flexlm_server)

TODAY = datetime.date(2099, 9, 1)

# lmutil lmstat -a|-i -c PORT@HOST: prints $FAKE_A or $FAKE_I and logs its arguments
FAKE_LMUTIL = """#!/bin/sh
echo "$* FLEXLM_TIMEOUT=$FLEXLM_TIMEOUT" >> "$FAKE_LOG"
[ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"
case "$2" in
    -a) cat "$FAKE_A" ;;
    -i) cat "$FAKE_I" ;;
esac
"""

# ssh [-o OPTION]... HOST COMMAND: runs COMMAND locally
FAKE_SSH = """#!/bin/sh
echo "ssh $*" >> "$FAKE_LOG"
[ -n "$FAKE_SSH_DOWN" ] && { echo "ssh: connect to host licsrv port 22: Connection refused" >&2; exit 255; }
for last; do :; done
sh -c "$last"
"""


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as handle:
        return handle.read()


def args(**kwargs):
    values = dict(license="27000@licsrv", warning=check_flexlm_server.Range.parse("30:"),
                  critical=check_flexlm_server.Range.parse("7:"))
    values.update(kwargs)
    return type("Args", (), values)


class TestCheck(unittest.TestCase):
    """check() on recorded lmstat output."""

    def check(self, a, i, today=TODAY, **kwargs):
        state, message, perf = check_flexlm_server.check(args(**kwargs), fixture(a), fixture(i), today)
        self.assertNotIn("\n", message)
        return state, message, perf

    def test_ok(self):
        state, message, perf = self.check("up_a.txt", "up_i.txt")
        self.assertEqual(check_flexlm_server.OK, state, message)
        self.assertEqual("4 feature(s), 3 of 16 licenses in use, SIMULINK expires on 2099-10-01, in 30 days",
                         message)

    def test_usage_of_all_features(self):
        state, message, perf = self.check("up_a.txt", "up_i.txt")
        self.assertEqual(["days_left=30;30;7", "'MATLAB'=2;;;0;10", "'SIMULINK'=0;;;0;5",
                          "'Signal_Toolbox'=1;;;0;1"], perf)

    def test_earliest_expiry_warning(self):
        # SIMULINK expires first, but only lmstat -i knows it: nobody uses it
        state, message, perf = self.check("up_a.txt", "up_i.txt", today=datetime.date(2099, 9, 10))
        self.assertEqual(check_flexlm_server.WARNING, state, message)
        self.assertTrue(message.startswith("SIMULINK expires on 2099-10-01, in 21 days, 4 feature(s)"), message)

    def test_earliest_expiry_critical(self):
        state, message, perf = self.check("up_a.txt", "up_i.txt", today=datetime.date(2099, 9, 28))
        self.assertEqual(check_flexlm_server.CRITICAL, state, message)
        self.assertIn("days_left=3;30;7", perf)

    def test_expired(self):
        state, message, perf = self.check("up_a.txt", "up_i.txt", today=datetime.date(2099, 10, 3))
        self.assertEqual(check_flexlm_server.CRITICAL, state, message)
        self.assertIn("SIMULINK expired on 2099-10-01, 2 days ago", message)
        self.assertIn("days_left=-2;30;7", perf)

    def test_expiry_from_lmstat_a_only(self):
        # lmstat -i failed: the expiry lines of lmstat -a are used
        state, message, perf = self.check("up_a.txt", "down_i.txt")
        self.assertEqual(check_flexlm_server.OK, state, message)
        self.assertIn("Signal_Toolbox expires on 2099-11-15", message)

    def test_permanent(self):
        state, message, perf = self.check("two_vendors_a.txt", "permanent_i.txt")
        self.assertEqual(check_flexlm_server.OK, state, message)
        self.assertIn("no feature expires", message)
        self.assertFalse([item for item in perf if item.startswith("days_left")], perf)

    def test_expiry_unknown(self):
        state, message, perf = self.check("two_vendors_a.txt", "down_i.txt")
        self.assertEqual(check_flexlm_server.UNKNOWN, state, message)
        self.assertIn("expiry dates unknown", message)

    def test_server_down(self):
        with self.assertRaises(check_flexlm_server.PluginError) as caught:
            self.check("down_a.txt", "down_i.txt")
        self.assertEqual(check_flexlm_server.CRITICAL, caught.exception.state)
        self.assertEqual('license server 27000@licsrv is down: Error getting status: Cannot connect to license '
                         'server system. (-15,570:111 "Connection refused")', caught.exception.message)

    def test_vendor_daemon_down(self):
        state, message, perf = self.check("vendor_down_a.txt", "up_i.txt")
        self.assertEqual(check_flexlm_server.CRITICAL, state, message)
        self.assertTrue(message.startswith("vendor daemon MLM is down: The desired vendor daemon is down. "
                                           "(-97,121), 4 feature(s)"), message)

    def test_triad_with_one_server_down(self):
        state, message, perf = self.check("triad_a.txt", "permanent_i.txt",
                                          license="27000@lic1,27000@lic2,27000@lic3")
        self.assertEqual(check_flexlm_server.WARNING, state, message)
        self.assertTrue(message.startswith("only 2 of 3 license servers up"), message)

    def test_same_feature_of_two_vendors(self):
        state, message, perf = self.check("two_vendors_a.txt", "permanent_i.txt")
        self.assertEqual(["'MLM/solver'=1;;;0;4", "'cdslmd/solver'=2;;;0;8", "'layout'=0;;;0;2"], perf)

    def test_no_features(self):
        text = fixture("up_a.txt").split("Feature usage info:")[0]
        with self.assertRaises(check_flexlm_server.PluginError) as caught:
            check_flexlm_server.check(args(), text, fixture("up_i.txt"), TODAY)
        self.assertEqual(check_flexlm_server.UNKNOWN, caught.exception.state)

    def test_parse_expiry(self):
        self.assertEqual(datetime.date(2026, 12, 31), check_flexlm_server.parse_expiry("31-dec-2026"))
        self.assertEqual(datetime.date(2026, 1, 5), check_flexlm_server.parse_expiry("5-JAN-2026"))
        self.assertEqual(datetime.date(1999, 12, 31), check_flexlm_server.parse_expiry("31-dec-99"))
        self.assertIsNone(check_flexlm_server.parse_expiry("1-jan-0"))
        self.assertIsNone(check_flexlm_server.parse_expiry("01-jan-0000"))
        self.assertIsNone(check_flexlm_server.parse_expiry("permanent(no expiration date)"))
        with self.assertRaises(ValueError):
            check_flexlm_server.parse_expiry("soon")


class TestCommandLine(unittest.TestCase):
    """The plugin as Nagios runs it."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.lmutil = self.executable("lmutil", FAKE_LMUTIL)
        self.executable("ssh", FAKE_SSH)
        self.log = os.path.join(self.tempdir, "log")
        open(self.log, "w").close()

    def tearDown(self):
        for name in os.listdir(self.tempdir):
            os.remove(os.path.join(self.tempdir, name))
        os.rmdir(self.tempdir)

    def executable(self, name, content):
        path = os.path.join(self.tempdir, name)
        with open(path, "w") as handle:
            handle.write(content)
        os.chmod(path, stat.S_IRWXU)
        return path

    def run_plugin(self, *argv, a="up_a.txt", i="up_i.txt", **env):
        environ = dict(os.environ, FAKE_A=os.path.join(FIXTURES, a), FAKE_I=os.path.join(FIXTURES, i),
                       FAKE_LOG=self.log, PATH=self.tempdir + os.pathsep + os.environ["PATH"], **env)
        result = subprocess.run([sys.executable, PLUGIN] + list(argv), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, env=environ)
        return result

    def logged(self):
        with open(self.log) as handle:
            return handle.read().splitlines()

    def test_ok(self):
        result = self.run_plugin("-l", self.lmutil, "-H", "licsrv")
        self.assertEqual(check_flexlm_server.OK, result.returncode, result)
        self.assertRegex(result.stdout, r"^FLEXLM OK - 4 feature\(s\), 3 of 16 licenses in use, SIMULINK expires "
                                        r"on 2099-10-01, in \d+ days \| days_left=\d+;30;7 'MATLAB'=2;;;0;10 ")
        self.assertEqual(1, len(result.stdout.splitlines()))
        self.assertEqual(["lmstat -a -c 27000@licsrv FLEXLM_TIMEOUT=30000000",
                          "lmstat -i -c 27000@licsrv FLEXLM_TIMEOUT=30000000"], self.logged())

    def test_port_timeout_and_triad(self):
        self.run_plugin("-l", self.lmutil, "-H", "lic1,lic2,lic3", "-p", "1055", "-t", "5")
        self.assertEqual("lmstat -a -c 1055@lic1,1055@lic2,1055@lic3 FLEXLM_TIMEOUT=5000000", self.logged()[0])

    def test_thresholds(self):
        result = self.run_plugin("-l", self.lmutil, "-H", "licsrv", "-w", "40000:", "-c", "1:")
        self.assertEqual(check_flexlm_server.WARNING, result.returncode, result)
        self.assertIn("days_left=", result.stdout)
        self.assertIn(";40000;1 ", result.stdout)

    def test_server_down(self):
        result = self.run_plugin("-l", self.lmutil, "-H", "licsrv", a="down_a.txt", i="down_i.txt")
        self.assertEqual(check_flexlm_server.CRITICAL, result.returncode, result)
        self.assertTrue(result.stdout.startswith("FLEXLM CRITICAL - license server 27000@licsrv is down: "),
                        result.stdout)

    def test_timeout(self):
        result = self.run_plugin("-l", self.lmutil, "-H", "licsrv", "-t", "1", FAKE_SLEEP="5")
        self.assertEqual(check_flexlm_server.CRITICAL, result.returncode, result)
        self.assertEqual("FLEXLM CRITICAL - no answer from 27000@licsrv within 1s\n", result.stdout)

    def test_lmutil_missing(self):
        result = self.run_plugin("-l", os.path.join(self.tempdir, "nothing"), "-H", "licsrv")
        self.assertEqual(check_flexlm_server.UNKNOWN, result.returncode, result)
        self.assertIn("is not an executable file", result.stdout)

    def test_ssh(self):
        result = self.run_plugin("-l", self.lmutil, "-H", "localhost", "-s", "monitor@licsrv", "-t", "7")
        self.assertEqual(check_flexlm_server.OK, result.returncode, result)
        log = self.logged()
        self.assertEqual(1, len([line for line in log if line.startswith("ssh ")]), log)
        self.assertTrue(log[0].startswith("ssh -o BatchMode=yes -o ConnectTimeout=7 monitor@licsrv "), log)
        self.assertEqual("lmstat -a -c 27000@localhost FLEXLM_TIMEOUT=7000000", log[1])

    def test_ssh_fails(self):
        result = self.run_plugin("-l", self.lmutil, "-H", "localhost", "-s", "licsrv", FAKE_SSH_DOWN="1")
        self.assertEqual(check_flexlm_server.UNKNOWN, result.returncode, result)
        self.assertEqual("FLEXLM UNKNOWN - cannot run lmutil on licsrv: ssh: connect to host licsrv port 22: "
                         "Connection refused\n", result.stdout)

    def test_usage_error_is_unknown(self):
        result = self.run_plugin("-H", "licsrv")
        self.assertEqual(check_flexlm_server.UNKNOWN, result.returncode, result)
        result = self.run_plugin("-l", self.lmutil, "-H", "licsrv", "-w", "abc")
        self.assertEqual(check_flexlm_server.UNKNOWN, result.returncode, result)
        self.assertEqual("FLEXLM UNKNOWN - invalid threshold 'abc'\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
