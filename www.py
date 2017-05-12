import os
import json
import shutil
import hashlib
import datetime
from urllib.parse import urlparse

import docker
import requests
from sparkpost import SparkPost
from slugify import slugify
from flask import Flask, request
from flask_slack import Slack
from concurrent.futures import ThreadPoolExecutor


app = Flask(__name__)
slack = Slack(app)
app.add_url_rule('/slack', view_func=slack.dispatch)
executor = ThreadPoolExecutor(2)
client = docker.from_env()

# sparkpost
sp = SparkPost()


@slack.command(
    'interview',
    token=os.getenv('SLACK_SLASH_TOKEN'),
    team_id=os.getenv('SLACK_SLASH_TEAM_ID'),
    methods=['POST']
)
def interview(**kwargs):
    hostname = urlparse(request.url_root).hostname
    text = kwargs.get('text')
    options = text.split()
    responseText = None
    if options[0] == 'list':
        responseText = ls()
    elif options[0] == 'start':
        responseText = 'Starting %s\'s interview.' % options[1]
        executor.submit(start, options[1], hostname)
    elif options[0] == 'stop':
        if len(options) == 1:
            responseText = stop()
        else:
            responseText = stop(options[1])
    else:
        responseText = 'Invalid action.'
    return slack.response(responseText)


def notify(text):
    requests.post(
        os.getenv('SLACK_INCOMING_HOOK'),
        data=json.dumps({'text': text}),
        headers={'Content-Type': 'application/json'}
    )

def ls():
    containers = client.containers.list(filters={
        'ancestor': 'jupyter/datascience-notebook'
    })
    if len(containers):
        responseText = '\n'.join([c.name for c in containers])
    else:
        responseText = 'No any interview.'
    return responseText


def stop(user_id=None):
    if user_id:
        container_name = slugify(user_id)
        container = client.containers.get(container_name)
        containers = [container]
    else:
        containers = client.containers.list(
            all=True,
            filters={
                'ancestor': 'jupyter/datascience-notebook'
            }
        )
    responseText = ""
    for container in containers:
        container.remove(force=True)
        responseText += '%s stopped.\n' % container.name
    return responseText


def start(user_id, hostname):
    container_name = slugify(user_id)
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        abspath = os.path.dirname(os.path.abspath(__file__))
        if user_id == 'admin':
            folder = abspath
        else:
            folder = '%s/jupyter/%s' % (abspath, user_id)
            try:
                shutil.copytree('%s/exam' % abspath, folder)
            except OSError as e:
                pass
                # notify('`%s` has already taken' % user_id)
            except Exception as e:
                notify(str(e))
        try:
            os.chown(folder, 0, 100)
            os.chmod(folder, int('775', 8))
        except Exception as e:
            notify(str(e))
        try:
            container = client.containers.run(
                'jupyter/datascience-notebook',
                name=container_name,
                cpuset_cpus="0",
                mem_limit="2500M",
                detach=True,
                publish_all_ports=True,
                volumes={folder: {'bind': '/home/jovyan/work', 'mode': 'rw'}}
            )
            container = client.containers.get(container.id)
        except Exception as e:
            notify(str(e))

    token = None
    ports = container.attrs['NetworkSettings']['Ports']
    port = ports['8888/tcp'][0]['HostPort']
    for line in container.logs(stream=True):
        pos = line.find(b'token=')
        if pos >= 0:
            pos += 6
            token = line[pos:pos + 48].decode("utf-8")
            break
    url = 'http://%s:%s/?token=%s' % (hostname, port, token)
    notify('%s\n%s' % (user_id, url))

    try:
        sp.transmissions.send(
            recipients=[user_id],
            template='data-science-online-assessment-url',
            substitution_data={
                'name': user_id,
                'url': url,
            }
        )
    except Exception as e:
        notify(str(e))

    return url


if __name__ == "__main__":
    app.run()
