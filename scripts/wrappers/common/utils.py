import getpass
import os
import platform
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import click
import yaml


kubeconfig = "--kubeconfig=" + os.path.expandvars("${SNAP_DATA}/credentials/client.config")


def get_current_arch():
    # architecture mapping
    arch_mapping = {'aarch64': 'arm64', 'x86_64': 'amd64'}

    return arch_mapping[platform.machine()]


def snap_data() -> Path:
    try:
        return Path(os.environ['SNAP_DATA'])
    except KeyError:
        return Path('/var/snap/microk8s/current')


def run(*args, die=True):
    # Add wrappers to $PATH
    env = os.environ.copy()
    env["PATH"] += ":%s" % os.environ["SNAP"]
    result = subprocess.run(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )

    try:
        result.check_returncode()
    except subprocess.CalledProcessError as err:
        if die:
            if result.stderr:
                print(result.stderr.decode("utf-8"))
            print(err)
            sys.exit(1)
        else:
            raise

    return result.stdout.decode("utf-8")


def is_cluster_ready():
    try:
        service_output = kubectl_get("all")
        node_output = kubectl_get("nodes")
        if "Ready" in node_output and "service/kubernetes" in service_output:
            return True
        else:
            return False
    except Exception:
        return False


def is_cluster_locked():
    if (snap_data() / 'var/lock/clustered.lock').exists():
        click.echo('This MicroK8s deployment is acting as a node in a cluster.')
        click.echo('Please use `microk8s enable` on the master.')
        sys.exit(1)


def wait_for_ready(wait_ready, timeout):
    start_time = time.time()
    isReady = False

    while True:
        if (timeout > 0 and (time.time() > (start_time + timeout))) or isReady:
            break
        try:
            isReady = is_cluster_ready()
        except Exception:
            time.sleep(2)

    return isReady


def exit_if_no_permission():
    user = getpass.getuser()
    # test if we can access the default kubeconfig
    clientConfigFile = os.path.expandvars("${SNAP_DATA}/credentials/client.config")
    if os.access(clientConfigFile, os.R_OK) == False:
        print("Insufficient permissions to access MicroK8s.")
        print(
            "You can either try again with sudo or add the user {} to the 'microk8s' group:".format(
                user
            )
        )
        print("")
        print("    sudo usermod -a -G microk8s {}".format(user))
        print("")
        print("The new group will be available on the user's next login.")
        exit(1)


def ensure_started():
    if (snap_data() / 'var/lock/stopped.lock').exists():
        click.secho('microk8s is not running, try microk8s start', fg='red', err=True)
        sys.exit(1)


def kubectl_get(cmd, namespace="--all-namespaces"):
    if namespace == "--all-namespaces":
        return run("kubectl", kubeconfig, "get", cmd, "--all-namespaces", die=False)
    else:
        return run("kubectl", kubeconfig, "get", cmd, "-n", namespace, die=False)


def kubectl_get_clusterroles():
    return run(
        "kubectl", kubeconfig, "get", "clusterroles", "--show-kind", "--no-headers", die=False
    )


def get_available_addons(arch):
    addon_dataset = os.path.expandvars("${SNAP}/addon-lists.yaml")
    available = []
    with open(addon_dataset, 'r') as file:
        # The FullLoader parameter handles the conversion from YAML
        # scalar values to Python the dictionary format
        addons = yaml.load(file, Loader=yaml.FullLoader)
        for addon in addons["microk8s-addons"]["addons"]:
            if arch in addon["supported_architectures"]:
                available.append(addon)

    available = sorted(available, key=lambda k: k['name'])
    return available


def get_addon_by_name(addons, name):
    filtered_addon = []
    for addon in addons:
        if name == addon["name"]:
            filtered_addon.append(addon)
    return filtered_addon


def xable(action: str, addons: list, xabled_addons: list):
    """Enables or disables the given addons.

    Collated into a single function since the logic is identical other than
    the script names.
    """
    actions = Path(__file__).absolute().parent / "../../../microk8s-resources/actions"
    existing_addons = {sh.with_suffix('').name[7:] for sh in actions.glob('enable.*.sh')}

    # Backwards compatibility with enabling multiple addons at once, e.g.
    # `microk8s.enable foo bar:"baz"`
    if all(a.split(':')[0] in existing_addons for a in addons) and len(addons) > 1:
        for addon in addons:
            if addon in xabled_addons and addon != 'kubeflow':
                click.echo("Addon %s is already %sd." % (addon, action))
            else:
                addon, *args = addon.split(':')
                subprocess.run([str(actions / ('%s.%s.sh' % (action, addon)))] + args)

    # The new way of xabling addons, that allows for unix-style argument passing,
    # such as `microk8s.enable foo --bar`.
    else:
        addon, *args = addons[0].split(':')

        if addon in xabled_addons and addon != 'kubeflow':
            click.echo("Addon %s is already %sd." % (addon, action))
            sys.exit(0)

        if addon not in existing_addons:
            click.secho("Addon `%s` not found." % addon, fg='red', err=True)
            click.echo("The available addons are:\n - %s" % '\n - '.join(existing_addons), err=True)
            sys.exit(1)

        if args and addons[1:]:
            click.secho(
                click.style(
                    "Can't pass string arguments and flag arguments simultaneously!\n", fg='red'
                )
                + textwrap.dedent(
                    """
                    {0} an addon with only one argument style at a time:

                        microk8s {1} foo:'bar'
                    or

                        microk8s {1} foo --bar
                """.format(
                        action.title(), action
                    )
                ),
                err=True,
            )
            sys.exit(1)

        script = [str(actions / ('%s.%s.sh' % (action, addon)))]
        if args:
            subprocess.run(script + args)
        else:
            subprocess.run(script + list(addons[1:]))
