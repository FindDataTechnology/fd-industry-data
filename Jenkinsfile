// fd-industry-runner image build. The pipeline text also lives in the Jenkins
// job `fd-industry-runner` (config held server-side like fd-law-data); keep
// the two in sync. Tags are immutable sha-<shorthash>.
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
        stage('Build runner image') {
            steps {
                sh 'docker build -t "${IMAGE}" .'
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
