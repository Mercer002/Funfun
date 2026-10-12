output "dynamodb_table_name" {
  description = "Name of the DynamoDB table storing ticker mentions"
  value       = aws_dynamodb_table.stock_mentions.name
}

output "dynamodb_table_arn" {
  description = "ARN of the DynamoDB table"
  value       = aws_dynamodb_table.stock_mentions.arn
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for stock scoring alerts"
  value       = aws_sns_topic.scoring_alerts.arn
}

output "lambda_function_name" {
  description = "Name of the deployed Lambda function"
  value       = aws_lambda_function.scoring_pipeline.function_name
}

output "lambda_function_arn" {
  description = "ARN of the deployed Lambda function"
  value       = aws_lambda_function.scoring_pipeline.arn
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge cron schedule rule"
  value       = aws_cloudwatch_event_rule.post_market_schedule.arn
}

