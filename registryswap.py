from flask import Flask, request, jsonify
from kubernetes import client, config
import base64
import copy
import datetime
import json
import logging
import os
import re
import ssl

import jsonpatch


app = Flask(__name__)

# Global variables
imageswap_pod_name = os.getenv("IMAGESWAP_POD_NAME")
registryswap_disable_label = os.getenv("REGISTRYSWAP_DISABLE_LABEL", "app.kubernetes.io/registryswap")
swap_registry_infra = os.getenv("REGISTRYSWAP_REGISTRY")
swap_port_infra     = os.getenv("REGISTRYSWAP_PORT")
swap_registryPort_infra = swap_registry_infra + ":" + swap_port_infra

swap_registry_user = os.getenv("REGISTRYSWAP_REGISTRY_USER")
swap_port_user     = os.getenv("REGISTRYSWAP_PORT_USER")
swap_registryPort_user = swap_registry_user + ":" + swap_port_user



@app.route('/', methods=["POST"])
def mutate():
    request_info    = request.json
    modified_spec   = copy.deepcopy(request_info)
    uid             = modified_spec["request"]["uid"]
    workload_type   = modified_spec["request"]["kind"]["kind"]
    namespace       = modified_spec["request"]["namespace"]
    workload_metadata = modified_spec["request"]["object"]["metadata"]

    needs_patch     = False

    if "name" in workload_metadata:
        workload = modified_spec["request"]["object"]["metadata"]["name"]

    elif "generateName" in workload_metadata:
        workload = modified_spec["request"]["object"]["metadata"]["generateName"]

    else:
        workload = uid

    app.logger.debug(json.dumps(request.json))

    # skip patch if disable label

    if( "labels" in workload_metadata
         and registryswap_disable_label in workload_metadata["labels"]
         and workload_metadata["labels"][registryswap_disable_label] == "disabled"):
        
        needs_patch = False

    else:

        if workload_type == "Pod":

            for container_spec in modified_spec["request"]["object"]["spec"]["containers"]:

                app.logger.info(f"swap container: {namespace}/{workload}")
                needs_patch = swap_registry(container_spec) or needs_patch

            if "initContainers" in  modified_spec["request"]["object"]["spec"]:

                for init_container_spec in modified_spec["request"]["object"]["spec"]["initContainers"]:
                    app.logger.info(f"swap init-container: {namespace}/{workload}")
                    needs_patch = swap_registry(container_spec) or needs_patch
        
        else:

            for container_spec in modified_spec["request"]["object"]["spec"]["template"]["spec"]["containers"]:
                
                app.logger.info(f"swap container: {namespace}/{workload}")
                needs_patch = swap_registry(container_spec) or needs_patch

            if "initContainers" in modified_spec["request"]["object"]["spec"]["template"]["spec"]:
                 
                for init_container_spec in modified_spec["request"]["object"]["spec"]["template"]["spec"]["initContainers"]:
                    app.logger.info(f"swap init-container: {namespace}/{workload}")
                    needs_patch = swap_registry(init_container_spec) or needs_patch
    
    if needs_patch:

        app.logger.info("patch container image registry")

        patch = jsonpatch.JsonPatch.from_diff(request_info["request"]["object"], modified_spec["request"]["object"])

        app.logger.debug(f"JSON Patch: {patch}")

        admission_response = {
            "allowed": True,
            "uid": request_info["request"]["uid"],
            "patch": base64.b64encode(str(patch).encode()).decode(),
            "patchType": "JSONPatch",
        }
        admissionReview = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": admission_response,
        }

    else:
        app.logger.info("Doesn't need patch")

        admission_response = {
            "allowed": True,
            "uid": request_info["request"]["uid"],
        }
        admissionReview = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": admission_response,
        }

    app.logger.debug(f"Admission Review: {json.dumps(admissionReview)}")

    return jsonify(admissionReview)

@app.route("/healthz", methods=["GET"])
def healthz():

    health_response = {
        "pod_name": imageswap_pod_name,
        "date_time": str(datetime.datetime.now()),
        "health": "ok",
    }

    # Return JSON formatted response object
    return jsonify(health_response)


def swap_registry(container_spec):

    name        = container_spec["name"]
    image       = container_spec["image"]
    new_image   = image
    swap_registryPort = ""
    infra, user = get_registry()

    image_split = image.partition("/")

    app.logger.debug(f"image_split: {image_split}")

    if "." in image_split[0] and image_split[1] != "" and image_split[2] != "":
        image_registry = image_split[0]
        image_name     = image_split[2]
    else:
        return False
    
    # check registry infra or user
    if image_registry == infra:
        swap_registryPort = swap_registryPort_infra
    else:
        swap_registryPort = swap_registryPort_user

    if image_registry == swap_registryPort:
        return False
    else:
      app.logger.info(f"Swapping image registry for container spec: {name}")
      new_image        = swap_registryPort + "/" + image_name

    container_spec["image"] = new_image
    return True

def get_registry():

    # Get registry address in crd
    config.load_incluster_config()
    api = client.CustomObjectsApi()

    pattern = '^(?:https?://)?'

    resource_infra = api.get_cluster_custom_object(
        group="management.accordions.co.kr",
        version="v1beta1",
        name="infra-registry",
        plural="registries",
    )
    infra_registry = resource_infra["entry"]["server"]

    infra_registry_split = infra_registry.partition(":")
    decode_infra = base64.b64decode(infra_registry_split[0]).decode()
    infra_registry_name = re.sub(pattern,"",decode_infra)

    app.logger.debug(f"infra-registry-name: {infra_registry_name}")

    resource_user = api.get_cluster_custom_object(
        group="management.accordions.co.kr",
        version="v1beta1",
        name="user-registry",
        plural="registries",
    )
    user_registry = resource_user["entry"]["server"]

    user_registry_split = user_registry.partition(":")
    decode_user = base64.b64decode(user_registry_split[0]).decode()
    user_registry_name = re.sub(pattern,"",decode_user)

    app.logger.debug(f"user-registry-name: {user_registry_name}")\

    return infra_registry_name, user_registry_name

def main():
##################################
# Webhook needs to serve TLS
##################################
    context = ssl.SSLContext(ssl.PROTOCOL_TLS)
    context.load_verify_locations('./ca.crt')
    context.load_cert_chain('./server.crt', './server.key')

    app.run(host="0.0.0.0", 
            port=5000,
            debug=True,
            ssl_context=context)

if __name__ == "__main__":
    
    main()
