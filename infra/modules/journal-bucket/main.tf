variable "name_prefix" {
  type = string
}

variable "common_tags" {
  type    = map(string)
  default = {}
}

resource "aws_s3_bucket" "journal" {
  bucket_prefix = "${var.name_prefix}-journal-"
  tags          = var.common_tags
}

resource "aws_s3_bucket_public_access_block" "journal" {
  bucket                  = aws_s3_bucket.journal.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "journal" {
  bucket = aws_s3_bucket.journal.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

output "bucket_name" {
  value = aws_s3_bucket.journal.id
}

output "bucket_arn" {
  value = aws_s3_bucket.journal.arn
}
