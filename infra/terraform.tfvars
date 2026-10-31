# Non-secret values only. google_client_secret, report_sender and
# report_recipients are HCP Terraform workspace variables.

project_name          = "peach"
region                = "us-east-1"
cognito_domain_prefix = "peach-melasya"
site_url              = "https://d2domne1zgw08y.cloudfront.net"
google_client_id      = "216982019038-ddcorrogt09a11s042bufdnnecd6d22u.apps.googleusercontent.com"

# The backend's network (peach-backend, CloudFormation).
vpc_id                     = "vpc-0b0c6322eb4c11d77"
subnet_ids                 = ["subnet-01b97808478b01ce2", "subnet-0adab98833b043d3e", "subnet-0d3bf129ed64fa774", "subnet-0e9a6784925ac69ba", "subnet-07ed459be1ed437db"]
route_table_ids            = ["rtb-03f9725eddd6301b7"]
function_security_group_id = "sg-018f31a9e7f1ee3e6"

# What the functions were created from; later code is deployed by the app pipeline.
builder_image_tag = "97079878ec36"
parser_image_tag  = "71631113a163"
mailer_code_key   = "mailer/mailer-87b1cbbdd4e2c4f0.zip"
