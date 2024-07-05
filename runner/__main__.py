import asyncio
import base64
from concurrent import futures
from pathlib import Path
import pickle
import subprocess
import sys
from typing import Mapping
import grpc
import requests
from mlab_pyprotos import runner_pb2_grpc, runner_pb2 
from runner.settings import settings
import runner.cog as cg

import inspect
import time
import multiprocessing
import logging

logging.basicConfig(level=logging.DEBUG)
class RunnerException(Exception):

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self._logger = logging.getLogger(__name__)

class Runner(runner_pb2_grpc.RunnerServicer):
    _server_monitor_url = 'http://197.255.122.208:61208/api/4/all'

    def __init__(self, workers_count: int=5) -> None:
        super().__init__()
        self.save_worker_count(workers_count)

    @staticmethod
    def logger():
        _logger = logging.getLogger(__name__)
        _logger.info('Starting')
        return _logger

    @staticmethod
    def save_worker_count(count) -> None:
        file_path = Path(f"{settings.runner_dir}/worker_count.pkl")
        with open(file_path, 'wb') as f:
            pickle.dump(count, f)
    
    @staticmethod
    def load_worker_count() -> int:
        file_path = Path(f"{settings.runner_dir}/worker_count.pkl")
        try:
            with open(file_path, 'rb') as f:
                return pickle.load(f)
        except FileNotFoundError:
            return 0
    
    @staticmethod
    def decrement_worker_count() -> int:
        workers_count = Runner.load_worker_count()
        workers_count = workers_count -1
        Runner.save_worker_count(workers_count)
        Runner.logger().debug(f"Worker count decrease: {workers_count}")
        return workers_count
    
    @staticmethod
    def increment_worker_count() -> int:
        workers_count = Runner.load_worker_count()
        workers_count = workers_count +1
        Runner.save_worker_count(workers_count)
        Runner.logger().debug(f"Worker count increase: {workers_count}")
        return workers_count
    
    @staticmethod
    def check_worker_count() -> bool:
        worker_count = Runner.load_worker_count()
        if worker_count < 0:
            Runner.logger().error("Not enough workers to create a task environment")
            raise RunnerException("Not enough workers to create a task environment")
        return True
    def get_runner(self, request, context):
        server_status = self._get_server_status()
        return runner_pb2.GetRunnerResponse(status=server_status)
    
    def stop_task(self, request, context):
        return super().stop_task(request, context)
    
    def remove_task(self, request, context):
        return super().remove_task(request, context)
    
    async def create_task_environment(self, request, context):
        self.check_worker_count()
        await cg.setup(request.job_id, request.dataset.name, request.model.name, request.dataset.branch, request.model.branch)
        Runner.increment_worker_count()
        return runner_pb2.CreateTaskResponse()
    
    def get_task_environment(self, request, context):
        return super().get_task_environment(request, context)
    
    async def run_task(self, request, context):
        self.check_worker_count()
        await cg.prepare(job_id=request.job_id, dataset_name=request.dataset.name, model_name=request.model.name, dataset_type=request.dataset.type, dataset_branch=request.dataset.branch, model_branch=request.model.branch, results_dir=request.results_dir)
        process: subprocess.Popen[bytes] = cg.run(name=request.task_name, at=request.model.path, task_id=request.task_id, user_id=request.user_id, base_dir=request.base_dir, dataset_dir=request.dataset.path, job_id=request.job_id, trained_model=request.trained_model)
        while self._stream_process(process):
            for line in process.stdout:
                yield runner_pb2.RunTaskResponse(line=line)
            time.sleep(0.1)
        Runner.logger().info("Task completed successfully")
        results = cg.fetch_results(request.model.path)
        if results is None:
            Runner.logger().error("No results")
        elif results[0] == "success":
            status, success = results
            Runner.logger().info("Results fetched successfully")
            files = []
            for key, value in success.get("files").items():
                info = runner_pb2.FileInfo(
                    name=key,
                    extension=key.split(".")[-1]
                )
                bytz = base64.b64decode(value)
                bytes_content = runner_pb2.BytesContent(
                    file_size=len(value),
                    buffer=bytz,
                    info=info,
                )
                files.append(bytes_content)
            metrics = []
            for key, value in success.get("metrics").items():
                print(key, value)
                metric = {
                    "name": key,
                    "value": value
                }
                metrics.append(metric)
            task_result = runner_pb2.TaskResult(
                task_id=success.get('task_id'),
                status=status,
                metrics=metrics,
                files=files,
                pkg_name=success.get('pkg_name'),
                pretrained_model=success.get('pretrained_model'),
            )
            print(metrics)
            yield runner_pb2.RunTaskResponse(result=task_result)
        else:
            Runner.logger().error("Error in return")
            status, error = results
            files = []
            for key, value in error.get("files").items():
                info = runner_pb2.FileInfo(
                    name=key,
                    extention=key.split(".")[-1]
                )
                bytes_content = runner_pb2.BytesContent(
                    file_size=len(value),
                    buffer=value,
                    info=info,
                )
                files.append(bytes_content)
            task_result = runner_pb2.TaskResult(
                task_id=error.get('task_id'),
                status=status,
                files=files,
                metrics=[],
                pkg_name=error.get('pkg_name'),
            )
            yield runner_pb2.RunTaskResponse(result=task_result)

        Runner.increment_worker_count()
        
    
    def _get_server_status(self):
        # res = requests.get(self._server_monitor_url)
        # print(res.json())
        # TODO: Function to calculate availability
        workers_count = self.load_worker_count()
        Runner.logger().debug(f"Current worker count: {workers_count} workers")
        return "available" if workers_count > 0 else "occupied"
    
    def _stream_process(self, process):
        go = process.poll() is None
        return go
    
async def serve():
    logger = logging.getLogger(__name__)
    server: grpc.aio.Server = grpc.aio.server(maximum_concurrent_rpcs=settings.workers_count)
    runner_pb2_grpc.add_RunnerServicer_to_server(Runner(), server)
    server.add_insecure_port('0.0.0.0:50051')
    logger.info('Runner server started on port 50051')
    try:
        await server.start()
        await server.wait_for_termination()
    except InterruptedError:
        pass
    finally:
        await server.stop(0)

if __name__ == '__main__':
    asyncio.run(serve())