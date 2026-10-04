// CI/CD for the DevOps Marathon apps (Doubt Wall + AIOps Triage).
//
//   Prepare -> Test -> Build images -> Security scan -> Push to ECR
//           -> Deploy:  DEPLOY_MODE=kubectl  Jenkins applies to k3s (auto-rollback)
//                       DEPLOY_MODE=gitops   Jenkins commits the new tag to git,
//                                            Argo CD deploys it
//           -> Smoke test
//
// Each stage's logic lives in ci/*.sh, so you can read it, run it by hand,
// and explain it without reading Groovy. Setup steps are in JENKINS.md.

pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()          // two deploys at once would fight over the cluster
    timeout(time: 30, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  parameters {
    booleanParam(name: 'DEPLOY', defaultValue: true,
                 description: 'Deploy after a successful build')
    choice(name: 'DEPLOY_MODE', choices: ['kubectl', 'gitops'],
           description: 'kubectl: Jenkins deploys to k3s. gitops: Jenkins commits the new tag, Argo CD deploys.')
    booleanParam(name: 'FAIL_ON_CRITICAL_VULNS', defaultValue: true,
                 description: 'Stop if Trivy finds a CRITICAL vulnerability that has a fix')
    string(name: 'APP_URL', defaultValue: '',
           description: 'k3s node address for the smoke test, e.g. http://13.234.10.20 (empty = skip)')
  }

  environment {
    // ECR_REGISTRY is set once under Manage Jenkins > System > Global properties,
    // e.g. 123456789012.dkr.ecr.ap-south-1.amazonaws.com
    AWS_REGION = "${env.AWS_REGION ?: 'ap-south-1'}"
    // SKIP_PIPELINE is deliberately NOT declared here: values from this block
    // can't be changed later with env.X = ... in a script, and Prepare sets it.
  }

  stages {
    stage('Prepare') {
      steps {
        script {
          // The GitOps stage pushes a "[skip ci]" commit. If that commit
          // triggers this job (webhook or polling), stop here instead of looping.
          def lastMessage = sh(script: 'git log -1 --pretty=%B', returnStdout: true).trim()
          if (lastMessage.contains('[skip ci]')) {
            env.SKIP_PIPELINE = 'true'
            currentBuild.result = 'NOT_BUILT'
            currentBuild.description = 'GitOps commit from Jenkins: nothing to build'
            echo 'Latest commit is a GitOps tag update ([skip ci]). Skipping all stages.'
            return
          }
          if (!env.ECR_REGISTRY?.trim()) {
            error('ECR_REGISTRY is not set. Add it under Manage Jenkins > System > Global properties.')
          }
          env.GIT_SHORT = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
          env.IMAGE_TAG = "${env.BUILD_NUMBER}-${env.GIT_SHORT}"
          currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.IMAGE_TAG}"
        }
        script {
          if (env.SKIP_PIPELINE != 'true') {
            sh 'docker version --format "Docker {{.Server.Version}}" && aws --version'
          }
        }
      }
    }

    stage('Test') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      steps {
        sh 'bash ci/test.sh'
      }
      post {
        always {
          junit allowEmptyResults: true, testResults: 'reports/*.xml'
        }
      }
    }

    stage('Build images') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      steps {
        sh 'bash ci/build.sh'
      }
    }

    stage('Security scan') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      environment {
        TRIVY_FAIL = "${params.FAIL_ON_CRITICAL_VULNS}"
      }
      steps {
        sh 'bash ci/scan.sh'
      }
      post {
        always {
          archiveArtifacts artifacts: 'reports/trivy-*.txt', allowEmptyArchive: true
        }
      }
    }

    stage('Push to ECR') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      steps {
        sh 'bash ci/push.sh'
      }
    }

    stage('Deploy to k3s') {
      when {
        allOf {
          not { environment name: 'SKIP_PIPELINE', value: 'true' }
          expression { params.DEPLOY && params.DEPLOY_MODE == 'kubectl' }
        }
      }
      steps {
        // A "Secret file" credential holding the k3s kubeconfig (see JENKINS.md).
        withCredentials([file(credentialsId: 'k3s-kubeconfig', variable: 'KUBECONFIG')]) {
          sh 'bash ci/deploy.sh'
        }
      }
    }

    stage('Update GitOps repo') {
      when {
        allOf {
          not { environment name: 'SKIP_PIPELINE', value: 'true' }
          expression { params.DEPLOY && params.DEPLOY_MODE == 'gitops' }
        }
      }
      steps {
        // A "Username with password" credential: GitHub user + a token that
        // can push to this repository (see PRACTICE-TASK.md, Part 6).
        withCredentials([usernamePassword(credentialsId: 'git-push',
                                          usernameVariable: 'GIT_USER',
                                          passwordVariable: 'GIT_TOKEN')]) {
          sh 'bash ci/gitops-update.sh'
        }
      }
    }

    stage('Smoke test') {
      when {
        allOf {
          not { environment name: 'SKIP_PIPELINE', value: 'true' }
          expression { params.DEPLOY }
          expression { params.APP_URL?.trim() }
        }
      }
      environment {
        // Argo CD needs time to notice the commit (it checks git every ~3 min).
        SMOKE_TIMEOUT = "${params.DEPLOY_MODE == 'gitops' ? '420' : '90'}"
      }
      steps {
        sh 'bash ci/smoke.sh'
      }
    }
  }

  post {
    always {
      sh 'docker image prune -f >/dev/null 2>&1 || true'
    }
    success {
      echo "Done: ${env.IMAGE_TAG}"
    }
    failure {
      echo 'Failed. Open the red stage above.'
      echo 'A failed rollout is rolled back automatically, so the previous version is still serving.'
    }
  }
}
