#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Python System imports
import subprocess
import argparse
import re
import threading
import socket
import time
import json
from pathlib import Path

# Third-party imports
import requests
import gitlab
from aiohttp import web


password = 'password'
session = requests.Session()
project_name = 'example repository'

CI_SCRIPT = '''\
success:
    script:
        - env

fail:
    script:
        - /bin/false

'''


def get_http_port():
    cmd = subprocess.run(['docker', 'ps', '-a'], stdout=subprocess.PIPE)

    for line in cmd.stdout.splitlines():
        if b'gitlab/gitlab-ce:' in line:
            match = re.search(br':(\d+)->80/tcp', line)
            if match:
                return int(match.group(1))
    raise ValueError('Port not found')


def base_url():
    return 'http://localhost:{}'.format(get_http_port())


def dockerhost_ip():
    # ip route | awk '/docker/ { print $NF }'
    cmd = subprocess.run(['ip', 'route'], stdout=subprocess.PIPE)
    for line in cmd.stdout.decode().splitlines():
        if 'docker' in line and 'src' in line:
            return line.split()[-1]


def get_available_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('localhost', 0))
    __, port = sock.getsockname()
    sock.close()
    return port


def dump(data, kind, name):
    output_path = Path(__file__).resolve().parent.parent / 'tests/data/gitlab' / kind / (name + '.json')

    with open(str(output_path), 'w') as dest:
        json.dump(data, dest, sort_keys=True, indent=4)


class HookCalls:

    count = 0
    expected_calls = []

    def incr(self):
        self.count += 1

    def expect_call(self, kind, name):
        self.expected_calls.append((kind, name))


hook_calls = HookCalls()


async def handle(request):
    data = await request.json()
    hook_calls.incr()

    kind = data['object_kind']
    expected_call = hook_calls.expected_calls[hook_calls.count - 1]
    if kind != expected_call[0]:
        print("Expected", expected_call, ", got", kind)
    else:
        dump(data, kind, expected_call[1])

    if kind == "push":
        print(kind, data['ref'], data['total_commits_count'], 'commits')
    elif kind == "tag_push":
        print(kind, data['ref'])
    elif kind == "merge_request":
        print(kind, data['object_attributes']['action'], data['object_attributes']['state'])
    elif kind == "issue":
        print(kind, data['object_attributes']['action'], data['object_attributes']['state'])
    elif kind == "note":
        print(kind, data['object_attributes']['noteable_type'])
    elif kind == "build":
        print(kind, 'stage:', data['build_stage'], data['build_name'], 'status:', data['build_status'])
    else:
        print("Unknown kind", kind)

    return web.Response(text="")


def server(port, **kwargs):
    app = web.Application()
    app.router.add_post('/', handle)

    loop = app.loop
    loop.run_until_complete(app.startup())
    handler = app.make_handler()
    srv = loop.run_until_complete(loop.create_server(handler, host='0.0.0.0', port=port, backlog=128))

    def launch():
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.close()
            loop.run_until_complete(srv.wait_closed())
            loop.run_until_complete(app.shutdown())
            loop.run_until_complete(handler.finish_connections(60))
            loop.run_until_complete(app.cleanup())
        loop.close()

    thread = threading.Thread(target=launch)
    thread.start()
    return loop


def run(*args, **kwargs):

    port = get_available_port()
    loop = server(port)
    hook_url = 'http://{}:{}'.format(dockerhost_ip(), port)

    try:
        gl = gitlab.Gitlab(base_url(), email='root', password=password)
        gl.auth()

        user = gl.users.list()[0]
        user.name = 'Example User'
        user.save()

        project = gl.projects.create({'name': project_name})

        try:
            project.hooks.create({
                'url': hook_url,
                'push_events': 1,
                'tag_push_events': 1,
                'issues_events': 1,
                'note_events': 1,
                'merge_requests_events': 1,
                'build_events': 1,
                'pipeline_events': 1,
                'wiki_events': 1,
            })

            ##############
            # push & tag_push events

            # 1 push message about new commit
            hook_calls.expect_call('push', 'first_commit')
            readme_file = project.files.create({
                'file_path': 'README.md',
                'branch_name': 'master',
                'content': 'My readme',
                'commit_message': 'Create project'
            })

            # 1 push message about new commit
            hook_calls.expect_call('push', 'commit_master_branch')
            readme_file.content = CI_SCRIPT + '\n# more content\n'
            readme_file.save(branch_name='master', commit_message='bump')

            # 1 tag_push message about new tag
            hook_calls.expect_call('tag_push', 'tag')
            project.tags.create({'tag_name': 'v0.1', 'ref': 'master'})

            # 1 push message about new commit, with total_commits_count = 0
            hook_calls.expect_call('push', 'create_dev_branch')
            project.branches.create({'branch_name': 'dev', 'ref': 'master'})

            # 1 push message about new commit
            hook_calls.expect_call('push', 'commit_dev_branch')
            readme_file.content = CI_SCRIPT + '\n# even more content\n'
            readme_file.save(branch_name='dev', commit_message='bump2 with unicode ⇗ ⟾')

            ##############
            # issue events

            # 1 issue event
            hook_calls.expect_call('issue', 'open_issue')
            issue = project.issues.create({'title': 'New ticket', 'description': 'Something useful here.'})

            # 1 issue event
            hook_calls.expect_call('issue', 'update_issue')
            issue.description = 'More useful description'
            issue.save()

            # close an issue
            # 2 issue events ('action': close, 'action': update)
            hook_calls.expect_call('issue', 'close_issue')
            hook_calls.expect_call('issue', 'close_update_issue')
            issue.state_event = 'close'
            issue.save()

            # reopen it
            # 2 issue events ('action': reopen, 'action': update)
            hook_calls.expect_call('issue', 'reopen_issue')
            hook_calls.expect_call('issue', 'reopen_update_issue')
            issue.state_event = 'reopen'
            issue.save()

            # 1 issue event
            hook_calls.expect_call('issue', 'label_issue')
            project.labels.create({'name': 'bugs', 'color': '#990000'})
            issue.labels = ['bugs']
            issue.save()

            ##############
            # note_events

            # 1 note event
            hook_calls.expect_call('note', 'issue_note')
            issue.notes.create({'body': 'new comment'})

            commits = project.commits.list()
            # 1 note event
            hook_calls.expect_call('note', 'commit_note')
            commits[-1].comments.create({'note': 'new comment'})

            ##############
            # merge request events

            # 1 merge request create
            hook_calls.expect_call('merge_request', 'open_merge_request')
            mr = project.mergerequests.create({
                'source_branch': 'dev',
                'target_branch': 'master',
                'title': 'merge cool feature'
            })

            # 1 merge request update
            hook_calls.expect_call('merge_request', 'update_merge_request')
            mr.description = 'New description'
            mr.save()

            # 2 merge request update
            hook_calls.expect_call('merge_request', 'close_merge_request')
            hook_calls.expect_call('merge_request', 'close_update_merge_request')
            mr.state_event = 'close'
            mr.save()

            # 2 merge request update
            hook_calls.expect_call('merge_request', 'reopen_merge_request')
            hook_calls.expect_call('merge_request', 'reopen_update_merge_request')
            mr.state_event = 'reopen'
            mr.save()

            # 1 note event
            hook_calls.expect_call('note', 'merge_request_note')
            mr.notes.create({'body': 'new comment'})

            # 1 merge request update
            # 1 push message about new commit
            hook_calls.expect_call('push', 'commit_merge_request')
            hook_calls.expect_call('merge_request', 'merge_merge_request')
            mr.merge()

            ##############
            # build events
            # 1 push message about new commit
            # 2 build messages for the 2 created builds

            hook_calls.expect_call('push', 'commit_for_build')
            hook_calls.expect_call('build', 'create_build_1')
            hook_calls.expect_call('build', 'create_build_2')
            ci_file = project.files.create({
                'file_path': '.gitlab-ci.yml',
                'branch_name': 'master',
                'content': CI_SCRIPT,
                'commit_message': 'Add CI'
            })

            cmd = [
                "docker", "exec", "-it", "gitlab-runner",
                "gitlab-ci-multi-runner", "register", "--non-interactive", "--executor", "shell", "--shell", "bash",
                "--url", "http://gitlab.example.com", "--name", "runner",
                "--registration-token", project.runners_token,
            ]
            # print(" ".join(cmd))
            subprocess.run(cmd, stdout=subprocess.PIPE)

            # 4 build events
            hook_calls.expect_call('build', 'start_build_1')
            hook_calls.expect_call('build', 'successful_build')
            hook_calls.expect_call('build', 'start_build_2')
            hook_calls.expect_call('build', 'failed_build')

            # give some time for the webhook calls
            while hook_calls.count < 5 + 7 + 2 + 9 + 3 + 4:
                time.sleep(1)

            gl.runners.list()[0].delete()

        finally:
            project.delete()

    finally:
        loop.stop()


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    password_parser = subparsers.add_parser('run')
    password_parser.set_defaults(func=run)

    server_parser = subparsers.add_parser('server')
    server_parser.add_argument('port')
    server_parser.set_defaults(func=server)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(**vars(args))


if __name__ == '__main__':
    main()
