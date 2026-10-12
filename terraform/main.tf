terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "MultiFactorStockScoringEngine"
      Version     = "2.0"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. DynamoDB State Table (7-Day Rolling Mentions)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "stock_mentions" {
  name         = var.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ticker"

  attribute {
    name = "ticker"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Name = var.dynamodb_table_name
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Amazon SNS Notification Topic
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_sns_topic" "scoring_alerts" {
  name = var.sns_topic_name

  tags = {
    Name = var.sns_topic_name
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. IAM Execution Role & Policies for AWS Lambda
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_iam_role" "lambda_exec_role" {
  name = "stock-scoring-lambda-exec-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# CloudWatch Logs Basic Execution Policy
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# DynamoDB Access Policy
resource "aws_iam_policy" "lambda_dynamodb_policy" {
  name        = "stock-scoring-dynamodb-policy-${var.environment}"
  description = "Allows Lambda to read/write rolling mentions in DynamoDB"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.stock_mentions.arn,
          "${aws_dynamodb_table.stock_mentions.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_dynamodb_attach" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.lambda_dynamodb_policy.arn
}

# SNS Publishing Policy
resource "aws_iam_policy" "lambda_sns_policy" {
  name        = "stock-scoring-sns-policy-${var.environment}"
  description = "Allows Lambda to publish winning stock payloads to SNS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = aws_sns_topic.scoring_alerts.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_sns_attach" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.lambda_sns_policy.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. AWS EventBridge Cron Schedule (Post-Market Close)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "post_market_schedule" {
  name                = "post-market-stock-scoring-rule-${var.environment}"
  description         = "Triggers the Stock Scoring Engine after market close on trading days"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.post_market_schedule.name
  target_id = "TriggerStockScoringLambda"
  arn       = aws_lambda_function.scoring_pipeline.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scoring_pipeline.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.post_market_schedule.arn
}

