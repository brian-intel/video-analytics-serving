'''
* Copyright (C) 2019-2020 Intel Corporation.
*
* SPDX-License-Identifier: BSD-3-Clause
'''

from collections.abc import MutableMapping
from collections import defaultdict
import os
import fnmatch
import string
from vaserving.common.utils import logging


class ModelsDict(MutableMapping):
    def __init__(self, model_name, model_version, *args, **kw):
        self._model_name = model_name
        self._model_version = model_version
        self._dict = dict(*args, **kw)

    def __setitem__(self, key, value):
        self._dict[key] = value

    def __delitem__(self, key):
        del self._dict[key]

    def __getitem__(self, key):
        if (key == "network"):
            if ('default' in self._dict["networks"]):
                return self._dict["networks"]["default"]
            return "{{models[{}][{}][VA_DEVICE_DEFAULT][network]}}".format(self._model_name,
                                                                           self._model_version)
        if (key in self._dict["networks"]):
            return self._dict["networks"][key]
        return self._dict.get(key, None)

    def __iter__(self):
        return iter(self._dict)

    def __len__(self):
        return len(self._dict)


class ModelManager:

    def __init__(self, model_dir, network_preference=None, ignore_init_errors=False):
        self.logger = logging.get_logger('ModelManager', is_static=True)
        self.model_dir = model_dir
        self.network_preference = network_preference
        self.models = defaultdict(dict)
        self.network_preference = {'CPU': ["FP32"],
                                   'HDDL': ["FP16"],
                                   'GPU': ["FP16"],
                                   'VPU': ["FP16"]}

        success = self.load_models(self.model_dir, network_preference)
        if (not ignore_init_errors) and (not success):
            raise Exception("Error Initializing Models")


    def _get_model_proc(self, path):
        candidates = fnmatch.filter(os.listdir(path), "*.json")
        if (len(candidates) > 1):
            raise Exception("Multiple model proc files found in %s" % (path,))
        if (len(candidates) == 1):
            return os.path.abspath(os.path.join(path, candidates[0]))
        return None

    def _get_model_network(self, path):
        candidates = fnmatch.filter(os.listdir(path), "*.xml")
        if (len(candidates) > 1):
            raise Exception("Multiple networks found in %s" % (path,))
        if(len(candidates) == 1):
            return os.path.abspath(os.path.join(path, candidates[0]))
        return None

    def _get_model_networks(self, path):
        networks = {}
        default = self._get_model_network(path)
        if (default):
            networks["default"] = default
        for network_type in os.listdir(path):
            network_type_path = os.path.join(path, network_type)
            if (os.path.isdir(network_type_path)):
                network = self._get_model_network(network_type_path)
                if (network):
                    networks[network_type] = {'network': network}
        return networks

    def get_network(self, model, network):
        preferred_model = model.replace("VA_DEVICE_DEFAULT", network)
        try:
            preferred_model = string.Formatter().vformat(
                preferred_model, [], {'models': self.models})
            return preferred_model
        except Exception:
            pass
        return None

    def get_default_network_for_device(self, device, model):
        if "VA_DEVICE_DEFAULT" in model:
            for preference in self.network_preference[device]:
                ret = self.get_network(model, preference)
                if ret:
                    return ret
                self.logger.info(
                    "Device preferred network {net} not found".format(net=preference))
            model = model.replace("[VA_DEVICE_DEFAULT]", "")
            self.logger.error("Could not resolve any preferred network {net}"
                              " for model {model}".format(
                                  net=self.network_preference[device], model=model))
        return model

    def load_models(self, model_dir, network_preference):
        #TODO: refactor
        #pylint: disable=R1702

        heading = "Loading Models"
        banner = "="*len(heading)
        self.logger.info(banner)
        self.logger.info(heading)
        self.logger.info(banner)
        error_occurred = False

        self.logger.info("Loading Models from Path {path}".format(
            path=os.path.abspath(self.model_dir)))
        if os.path.islink(self.model_dir):
            self.logger.warning("Models directory is symbolic link")
        if os.path.ismount(self.model_dir):
            self.logger.warning("Models directory is mount point")
        models = defaultdict(dict)
        if (network_preference):
            for key in network_preference:
                if (isinstance(network_preference[key], str)):
                    network_preference[key] = network_preference[key].split(
                        ',')
            self.network_preference.update(network_preference)
        for model_name in os.listdir(model_dir):
            try:
                model_path = os.path.join(model_dir, model_name)

                if (not os.path.isdir(model_path)):
                    continue

                for version in os.listdir(model_path):
                    version_path = os.path.join(model_path, version)
                    if (os.path.isdir(version_path)):
                        version = int(version)
                        proc = self._get_model_proc(version_path)
                        networks = self._get_model_networks(
                            version_path)
                        if (proc) and (networks):
                            for key in networks:
                                networks[key].update({"proc": proc,
                                                      "version": version,
                                                      "type": "IntelDLDT",
                                                      "description": model_name})

                            models[model_name][version] = ModelsDict(model_name,
                                                                     version,
                                                                     {"networks": networks,
                                                                      "proc": proc,
                                                                      "version": version,
                                                                      "type": "IntelDLDT",
                                                                      "description": model_name
                                                                      })
                            network_paths = {
                                key: value["network"] for key, value in networks.items()}
                            network_paths["model-proc"] = proc
                            self.logger.info("Loading Model: {} version: {} "
                                             "type: {} from {}".format(
                                                 model_name, version, "IntelDLDT", network_paths))
                        else:
                            raise Exception("%s/%s is missing Model-Proc or Network" % (model_name, version))

            except Exception as error:
                error_occurred = True
                self.logger.error("Error Loading Model {model_name}"
                                  " from: {model_dir}: {err}".format(
                                      err=error, model_name=model_name, model_dir=model_dir))

        self.models = models

        heading = "Completed Loading Models"
        banner = "="*len(heading)
        self.logger.info(banner)
        self.logger.info(heading)
        self.logger.info(banner)
        return not error_occurred

    def get_model_parameters(self, name, version):
        if name not in self.models or version not in self.models[name]:
            return None
        params_obj = {
            "name": name,
            "version": version
        }

        if "networks" in self.models[name][version]:
            proc = None
            for _, value in self.models[name][version]['networks'].items():
                proc = value['proc']
                break
            params_obj["networks"] = {
                'model-proc': proc,
                'networks': {key: value['network']
                             for key, value
                             in self.models[name][version]['networks'].items()}}

        if "type" in self.models[name][version]:
            params_obj["type"] = self.models[name][version]["type"]

        if "description" in self.models[name][version]:
            params_obj["description"] = self.models[name][version]["description"]
        return params_obj

    def get_loaded_models(self):
        results = []
        if self.models is not None:
            for model in self.models:
                for version in self.models[model].keys():
                    result = self.get_model_parameters(model, version)
                    if result:
                        results.append(result)
        return results
