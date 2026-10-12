variable "aws_region" {
  description = "The AWS region to deploy resources in"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name (e.g. prod, staging, dev)"
  type        = string
  default     = "prod"
}

variable "dynamodb_table_name" {
  description = "Name of the DynamoDB table storing 7-day rolling ticker mentions"
  type        = string
  default     = "StockMentions"
}

variable "sns_topic_name" {
  description = "Name of the Amazon SNS topic for high-conviction alerts"
  type        = string
  default     = "stock-scoring-alerts"
}

variable "schedule_expression" {
  description = "EventBridge cron schedule expression to run post-market close (UTC)"
  type        = string
  default     = "cron(0 21 ? * MON-FRI *)" # 4:00 PM EST / 5:00 PM EDT Monday-Friday
}

variable "groq_api_key" {
  description = "API key for Groq Llama 3 inference"
  type        = string
  default     = ""
  sensitive   = true
}

variable "groq_model" {
  description = "Groq model identifier for catalyst extraction"
  type        = string
  default     = "llama-3.3-70b-versatile"
}

variable "reddit_client_id" {
  description = "PRAW Reddit OAuth Client ID"
  type        = string
  default     = ""
}

variable "reddit_client_secret" {
  description = "PRAW Reddit OAuth Client Secret"
  type        = string
  default     = ""
  sensitive   = true
}

variable "reddit_user_agent" {
  description = "User agent string for Reddit scraping"
  type        = string
  default     = "StockScoringEngine/2.0"
}

variable "discord_webhook_url" {
  description = "Discord incoming webhook URL for alert routing"
  type        = string
  default     = ""
  sensitive   = true
}

variable "spike_threshold_percent" {
  description = "Minimum velocity percentage spike to trigger Module 2 LLM analysis"
  type        = number
  default     = 150.0
}

variable "total_score_threshold" {
  description = "Minimum composite score threshold required to dispatch alerts"
  type        = number
  default     = 75.0
}

