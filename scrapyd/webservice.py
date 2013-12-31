import traceback
import uuid
from cStringIO import StringIO

from twisted.python import log

#from scrapy.utils.txweb import JsonResource
from .utils import get_spider_list
from pprint import pprint
import base64
from twisted.cred.error import Unauthorized
import json
from twisted.web import resource

from db import authenticate, update_spiders, add_job

class JsonResource(resource.Resource):

    json_encoder = json.JSONEncoder()

    def render(self, txrequest):
        r = resource.Resource.render(self, txrequest)
        return self.render_object(r, txrequest)

    def render_object(self, obj, txrequest):
        r = self.json_encoder.encode(obj) + "\n"
        txrequest.setHeader('Content-Type', 'application/json,application/x-www-form-urlencoded')
        txrequest.setHeader('Access-Control-Allow-Origin', '*')
        txrequest.setHeader('Access-Control-Allow-Methods', 'GET, POST, PATCH, PUT, DELETE')
        txrequest.setHeader('Access-Control-Allow-Headers','Content-Type,X-Requested-With,Authorization')
        txrequest.setHeader('Content-Length', len(r))
        return r

class WsResource(JsonResource):

    def __init__(self, root):
        JsonResource.__init__(self)
        self.root = root

    def login(self, email, password):
        projects = authenticate(email, password)
        if projects:
            self.projects = projects
            return True
        else:
            raise Unauthorized('Wrong credentials')

    def render(self, txrequest):
        try:
            print "Request Method = "+txrequest.method
            if txrequest.method != 'OPTIONS':
                self.login(txrequest.getUser(), txrequest.getPassword())
            return JsonResource.render(self, txrequest)
        except Exception, e:
            if self.root.debug:
                return traceback.format_exc()
            log.err()
            r = {"status": "error", "message": str(e)}
            return self.render_object(r, txrequest)

    def render_OPTIONS(self,txrequest):
        return self.render_object({'status':'success'},txrequest)

class Schedule(WsResource):

    def render_POST(self, txrequest):
        settings = txrequest.args.pop('setting', [])
        settings = dict(x.split('=', 1) for x in settings)
        args = dict((k, v[0]) for k, v in txrequest.args.items())
        project = args.pop('project')
        if project not in self.projects:
            return {'status': "error", 'message': "Invalid project"}
        spider = args.pop('spider')
        args['settings'] = settings
        jobid = uuid.uuid1().hex
        args['_job'] = jobid
        self.root.scheduler.schedule(project, spider, **args)
        add_job(project, spider, jobid)
        return {"status": "ok", "jobid": jobid}

class Cancel(WsResource):

    def render_POST(self, txrequest):
        args = dict((k, v[0]) for k, v in txrequest.args.items())
        project = args['project']
        jobid = args['job']
        signal = args.get('signal', 'TERM')
        prevstate = None
        queue = self.root.poller.queues[project]
        c = queue.remove(lambda x: x["_job"] == jobid)
        if c:
            prevstate = "pending"
        spiders = self.root.launcher.processes.values()
        for s in spiders:
            if s.job == jobid:
                s.transport.signalProcess(signal)
                prevstate = "running"
        return {"status": "ok", "prevstate": prevstate}

class AddVersion(WsResource):
    #implement auth here

    def render_POST(self, txrequest):
        project = txrequest.args['project'][0]
        if project not in self.projects:
            return {'status': "error", 'message': "Invalid project"}
        version = txrequest.args['version'][0]
        eggf = StringIO(txrequest.args['egg'][0])
        self.root.eggstorage.put(eggf, project, version)
        spiders = get_spider_list(project)
        update_spiders(project, spiders)
        self.root.update_projects()
        return {"status": "ok", "project": project, "version": version,
                "spiders": len(spiders)}


class ListProjects(WsResource):

    def render_GET(self, txrequest):
        projects = self.root.scheduler.list_projects()
        return {"status": "ok", "projects": projects}

class ListVersions(WsResource):

    def render_GET(self, txrequest):
        project = txrequest.args['project'][0]
        versions = self.root.eggstorage.list(project)
        return {"status": "ok", "versions": versions}

class ListSpiders(WsResource):

    def render_GET(self, txrequest):
        project = txrequest.args['project'][0]
        spiders = get_spider_list(project, runner=self.root.runner)
        return {"status": "ok", "spiders": spiders}

class ListJobs(WsResource):

    def render_GET(self, txrequest):
        project = txrequest.args['project'][0]
        spiders = self.root.launcher.processes.values()
        running = [{"id": s.job, "spider": s.spider,
            "start_time": s.start_time.isoformat(' ')} for s in spiders if s.project == project]
        queue = self.root.poller.queues[project]
        pending = [{"id": x["_job"], "spider": x["name"]} for x in queue.list()]
        finished = [{"id": s.job, "spider": s.spider,
            "start_time": s.start_time.isoformat(' '),
            "end_time": s.end_time.isoformat(' ')} for s in self.root.launcher.finished
            if s.project == project]
        return {"status":"ok", "pending": pending, "running": running, "finished": finished}

class DeleteProject(WsResource):

    def render_POST(self, txrequest):
        project = txrequest.args['project'][0]
        self._delete_version(project)
        return {"status": "ok"}

    def _delete_version(self, project, version=None):
        self.root.eggstorage.delete(project, version)
        self.root.update_projects()

class DeleteVersion(DeleteProject):

    def render_POST(self, txrequest):
        project = txrequest.args['project'][0]
        version = txrequest.args['version'][0]
        self._delete_version(project, version)
        return {"status": "ok"}
