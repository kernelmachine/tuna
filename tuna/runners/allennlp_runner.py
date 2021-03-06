import os
import argparse
import logging
import _jsonnet
import json

from datetime import datetime
from allennlp.common.params import Params, parse_overrides, with_fallback
from allennlp.commands.train import train_model
from allennlp.common.util import import_submodules
from tuna.runners import Runner
from tuna.random_search import HyperparameterSearch
from tuna.search_environments import SEARCH_ENVIRONMENTS

from typing import Optional

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

def is_s3_url(path):
    return path[:1] == 's3'

class AllenNlpRunner(Runner):
    name = "AllenNLP"

    def get_argument_parser(self) -> Optional[argparse.ArgumentParser]:
        parser = argparse.ArgumentParser()

        parser.add_argument(
            "--parameter-file",
            required=True,
            type=os.path.abspath,
            help="path to parameter file describing the model to be trained",
        )
        
        parser.add_argument(
            "--include-package",
            type=str,
            action="append",
            default=[],
            help="additional packages to include",
        )
        parser.add_argument(
            "-o",
            "--overrides",
            type=str,
            default="",
            help="a JSON structure used to override the experiment configuration",
        )
        return parser

    def get_run_func(
        self,
        default_args: argparse.Namespace,
        run_args: Optional[argparse.Namespace] = None,
    ):
        if run_args is None:
            raise ValueError("No run arguments found for AllenNLP runner.")

        with open(run_args.parameter_file, "r") as parameter_f:
            parameter_file_snippet = parameter_f.read()

        def train_func(config, reporter):
            logger.debug(f"CUDA_VISIBLE_DEVICES: {os.environ['CUDA_VISIBLE_DEVICES']}")

            for package_name in getattr(run_args, "include_package", ()):
                import_submodules(package_name)

            run_parameters = {k: json.dumps(v) for k, v in config.items()}
            search_config = SEARCH_ENVIRONMENTS[default_args.search_config]
            search_space = HyperparameterSearch(**search_config)
            sample = search_space.sample()
            logger.info(f"Hyperparameter Configuration: {sample}")

            for k, v in sample.items():
                sample[k] = str(v)
                os.environ[k] = str(v)

            params_dict = json.loads(
                _jsonnet.evaluate_snippet(
                    "config", parameter_file_snippet, tla_codes=run_parameters, ext_vars=sample
                )
            )
            if default_args.num_gpus == 0:
                logger.warning(f"No GPU specified, using CPU.")
                params_dict["trainer"]["cuda_device"] = -1

            # Make sure path is absolute (as Ray workers do not use the same working dir)
            train_data_path = params_dict["train_data_path"]
            validation_data_path = params_dict.get("validation_data_path")

            # if not os.path.isabs(train_data_path) and not is_s3_url(train_data_path):
            #     params_dict["train_data_path"] = os.path.abspath(
            #         os.path.join(default_args.cwd, train_data_path)
            #     )

            # if validation_data_path and not os.path.isabs(validation_data_path) and not is_s3_url(validation_data_path):
            #     params_dict["validation_data_path"] = os.path.abspath(
            #         os.path.join(default_args.cwd, validation_data_path)
            #     )

            params = Params(params_dict)

            logger.debug(f"AllenNLP Configuration: {params.as_dict()}")

            train_model(params=params, serialization_dir="./trial/")

            reporter(done=True)

        return train_func
