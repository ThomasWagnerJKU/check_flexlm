# check_flexlm

A Nagios/Icinga plugin that checks a FlexLM (FlexNet Publisher) license server
with Flexera's `lmutil`. Python 3.7 or newer, standard library only.

It checks that the license server and all vendor daemons are up, reports the
usage of every feature as performance data, and alerts on the feature that
expires first.

## Usage

```sh
check_flexlm -l /opt/flexlm/lmutil -H licsrv.example.com
check_flexlm -l /opt/flexlm/lmutil -H lic1,lic2,lic3 -p 27000 -w 60: -c 14:
check_flexlm -l /opt/flexlm/lmutil -H localhost -s monitor@licsrv.example.com
```

```
FLEXLM OK - 4 feature(s), 3 of 16 licenses in use, SIMULINK expires on 2026-12-31, in 94 days | days_left=94;30;7 'MATLAB'=2;;;0;10 'SIMULINK'=0;;;0;5 'Signal_Toolbox'=1;;;0;1
```

| Option | Meaning |
|---|---|
| `-l`, `--lmutil` | Path of `lmutil` (required); with `-s`, the path on that host |
| `-H`, `--hostname` | License server (required); a comma separated list for a redundant triad |
| `-p`, `--port` | Port of lmgrd (default: 27000) |
| `-w`, `--warning` | Warning range for the days until the earliest expiry (default: `30:`) |
| `-c`, `--critical` | Critical range for the days until the earliest expiry (default: `7:`) |
| `-t`, `--timeout` | Timeout in seconds for each lmutil call (default: 30) |
| `-s`, `--ssh` | Run lmutil on this `[USER@]HOST` via ssh, see below |
| `-V`, `--version` | Show the version |

The plugin runs `lmutil lmstat -a -c PORT@HOST` for the state of the servers,
the vendor daemons and the usage of each feature, and `lmutil lmstat -i -c
PORT@HOST` for the expiry dates of all features, including those nobody uses.

### Thresholds

`-w` and `-c` take the
[monitoring plugins range format](https://www.monitoring-plugins.org/doc/guidelines.html#THRESHOLDFORMAT)
and apply to the number of days until the earliest expiry date of any
feature. As fewer days are worse, use lower bounds: `30:` alerts below 30
days. An expired feature has a negative number of days left. Features that
never expire are not considered; if none expires, the expiry check is OK.

### Performance data

- `days_left`: days until the earliest expiry, with the thresholds
- one entry per counted feature: licenses in use, with 0 and the number of
  issued licenses as minimum and maximum. A feature name that two vendor
  daemons share is prefixed with the vendor, e.g. `'MLM/solver'`. Uncounted,
  node-locked features have no usage to report.

### States

- **CRITICAL**: the license server is down or does not answer within `-t`, a
  vendor daemon is down, or the earliest expiry is outside `-c`
- **WARNING**: the earliest expiry is outside `-w`, or only two of the three
  servers of a triad are up
- **UNKNOWN**: lmutil cannot be run, the server reports no features, the
  expiry dates cannot be read, or the command line is wrong

The plugin prints exactly one line.

## Without lmutil on the monitoring host

FlexLM's protocol between lmutil and lmgrd is proprietary and undocumented,
and there is no maintained open implementation of it, so the plugin needs
`lmutil`. It does not have to be on the monitoring host, though:

- **`-s`/`--ssh`**: the plugin runs lmutil on another host, typically the
  license server itself, where lmutil is installed alongside lmgrd. `-l` is
  then the path of lmutil on that host and `-H` is resolved there, so
  `-H localhost` checks the server the ssh connection goes to. Both lmstat
  calls share one ssh connection, which runs with `BatchMode` and fails
  instead of asking for a password or host key. The monitoring user needs key
  based access; restrict the key on the license server with
  `command=`/`restrict` in `authorized_keys` if you like.
- **An agent on the license server**: run check_flexlm there via NRPE, the
  Icinga 2 agent or check_by_ssh, with a local `-l`.
- **Liveness only**: `check_tcp` against the lmgrd port (27000) and the vendor
  daemon port (set with `VENDOR ... port=` in the license file) tells whether
  the daemons listen, but not their usage or expiry.

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests replay recorded lmstat output through a fake lmutil and a fake ssh.

## License

GPL-2.0-or-later, see [LICENSE](LICENSE).
