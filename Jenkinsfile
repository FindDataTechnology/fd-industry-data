// fd-industry-runner image build + content admission gate. The pipeline text
// also lives in the Jenkins job `fd-industry-runner`; keep the two in sync.
// Order matters: content checks and the in-image import scan run BEFORE push,
// so a red gate never publishes an image. Tags are immutable sha-<shorthash>.
pipeline {
    agent any
    parameters {
        string(name: 'REPO_URL', defaultValue: 'git@gitee.com:FindDataTechnology/fd-industry-data.git', description: 'Gitee repo (SSH)')
        string(name: 'BRANCH', defaultValue: 'main', description: 'Branch to build')
        string(name: 'IMAGE_TAG', defaultValue: '', description: 'Image tag override; empty = sha-<short-hash>')
    }
    environment {
        REPO = "${params.REPO_URL}"
        BR = "${params.BRANCH}"
        HARBOR = '100.64.0.8:30880'
        IMAGE_BASE = '100.64.0.8:30880/finddata/fd-industry-runner'
        PYIMG = 'docker.m.daocloud.io/library/python:3.12-slim'
        TSINGHUA = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    }
    stages {
        stage('Checkout') {
            steps {
                checkout([$class: 'GitSCM',
                    branches: [[name: "${BR}"]],
                    userRemoteConfigs: [[url: "${REPO}", credentialsId: 'gitee-ssh']]])
            }
        }
        stage('Show Commit') {
            steps {
                script {
                    def commit = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
                    echo "COMMIT: ${commit}"
                    if (params.IMAGE_TAG?.trim()) {
                        env.IMAGE_TAG = params.IMAGE_TAG
                    } else {
                        env.IMAGE_TAG = 'sha-' + sh(returnStdout: true, script: 'git rev-parse --short HEAD').trim()
                    }
                    env.IMAGE = "${env.IMAGE_BASE}:${env.IMAGE_TAG}"
                    echo "IMAGE: ${env.IMAGE}"
                }
            }
        }
        stage('Gate: manifest v2 + conformance') {
            steps {
                sh '''#!/bin/bash
                    set -e
                    docker run --rm -v "$PWD":/w -w /w ${PYIMG} sh -c \
                      "pip install -q -i ${TSINGHUA} pyyaml && python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py"
                '''
            }
        }
        stage('Build runner image') {
            steps {
                sh 'docker build -t "${IMAGE}" .'
            }
        }
        stage('Gate: import scan in built image') {
            steps {
                sh '''#!/bin/bash
                    set -e
                    docker run --rm -v "$PWD/spiders:/content/spiders" -w /content -e FD_CONTENT_DIR=/content/spiders \
                      "${IMAGE}" python - <<'PY'
import importlib, pathlib, sys
failed = []
for d in sorted(pathlib.Path("/content/spiders").iterdir()):
    if not (d / "spider.py").is_file() or d.name.startswith((".", "_")):
        continue
    name = f"spiders.{d.name.replace('-', '_')}.spider"
    try:
        importlib.import_module(name)
    except Exception as e:
        failed.append(f"{d.name}: {type(e).__name__}: {e}")
if failed:
    print("\\n".join(failed), file=sys.stderr)
    print(f"import scan: {len(failed)} failed of scanned", file=sys.stderr)
    sys.exit(1)
print("import scan: all spider modules import cleanly")
PY
                '''
            }
        }
        stage('Push runner image') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'harbor-fd-push', usernameVariable: 'HARBOR_USER', passwordVariable: 'HARBOR_PASS')]) {
                    sh '''#!/bin/bash
                        set -e
                        echo "${HARBOR_PASS}" | docker login "${HARBOR}" -u "${HARBOR_USER}" --password-stdin
                        docker push "${IMAGE}"
                        docker logout "${HARBOR}" || true
                    '''
                }
            }
        }
    }
}
