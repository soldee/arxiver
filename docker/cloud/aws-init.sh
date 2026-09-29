#!/bin/bash
set -e

REQUIRED_VARS=(
    "CLOUD__SQS_QUEUE_NAME"
    "CLOUD__S3_BUCKET_NAME"
    "AWS_DEFAULT_REGION"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo "[aws-init.sh] [ERROR] Required environment variable '$var' is not set or empty." >&2
        exit 1
    fi
done

# this localstack image ships with aws version < 2, which does not check AWS_ENDPOINT_URL env variable
aws () {
    command aws --endpoint-url="${AWS_ENDPOINT_URL}" "$@"
}

echo "[aws-init.sh] Creating upload file event SQS"
aws sqs create-queue --queue-name ${CLOUD__SQS_QUEUE_NAME}

SQS_URL=$(aws sqs get-queue-url \
    --queue-name ${CLOUD__SQS_QUEUE_NAME}\
    --query QueueUrl\
    --output text
)

echo "[aws-init.sh] Get ARN identifier for upload file event SQS"
SQS_ARN=$(aws --endpoint-url=${AWS_ENDPOINT_URL} sqs get-queue-attributes\
    --queue-url ${SQS_URL}\
    --attribute-names QueueArn\
    --query "Attributes.QueueArn"\
    --output text
)

echo "[aws-init.sh] Listing queues"
aws sqs list-queues

echo "[aws-init.sh] Creating S3 bucket"
if [ "${AWS_DEFAULT_REGION}" = "us-east-1" ] || [ -z "${AWS_DEFAULT_REGION}" ]; then
    aws s3api create-bucket \
        --bucket "${CLOUD__S3_BUCKET_NAME}"
else
    aws s3api create-bucket \
        --bucket "${CLOUD__S3_BUCKET_NAME}" \
        --create-bucket-configuration LocationConstraint="${AWS_DEFAULT_REGION}"
fi

echo "[aws-init.sh] Listing S3 bucket"
aws s3api list-buckets

echo "[aws-init.sh] Setting S3 bucket notifications"
aws s3api put-bucket-notification-configuration\
    --bucket ${CLOUD__S3_BUCKET_NAME}\
    --notification-configuration  \
    '{
        "QueueConfigurations": [
        {
            "QueueArn": "'"$SQS_ARN"'",
            "Events": ["s3:ObjectCreated:*"]
        }
       ]
    }'

echo "[aws-init.sh] Get S3 bucket notifications"
aws s3api get-bucket-notification-configuration\
    --bucket $CLOUD__S3_BUCKET_NAME