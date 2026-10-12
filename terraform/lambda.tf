# Package Lambda source code into a deployment zip
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/lambda_payload.zip"
}

# AWS Lambda Function (Python 3.11)
resource "aws_lambda_function" "scoring_pipeline" {
  function_name = "stock-scoring-pipeline-${var.environment}"
  role          = aws_iam_role.lambda_exec_role.arn
  handler       = "lambda_handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 512

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      ENVIRONMENT             = var.environment
      STOCK_MENTIONS_TABLE    = aws_dynamodb_table.stock_mentions.name
      AWS_REGION              = var.aws_region
      SNS_TOPIC_ARN           = aws_sns_topic.scoring_alerts.arn
      GROQ_API_KEY            = var.groq_api_key
      GROQ_MODEL              = var.groq_model
      REDDIT_CLIENT_ID        = var.reddit_client_id
      REDDIT_CLIENT_SECRET    = var.reddit_client_secret
      REDDIT_USER_AGENT       = var.reddit_user_agent
      DISCORD_WEBHOOK_URL     = var.discord_webhook_url
      SPIKE_THRESHOLD_PERCENT = tostring(var.spike_threshold_percent)
      TOTAL_SCORE_THRESHOLD   = tostring(var.total_score_threshold)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_logs,
    aws_iam_role_policy_attachment.lambda_logs
  ]
}

# CloudWatch Log Group for Lambda
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/stock-scoring-pipeline-${var.environment}"
  retention_in_days = 14
}

