
#vpc
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
  instance_tenancy = "default"
}

#cgw
resource "aws_customer_gateway" "cgw_1" {
  bgp_asn    = 1
  ip_address = "1.1.1.1"
  type       = "ipsec.1"
}

resource "aws_ec2_transit_gateway" "tgw" {
  amazon_side_asn                 = 65162
  default_route_table_association = "disable"
  default_route_table_propagation = "disable"
  auto_accept_shared_attachments  = "enable"
  dns_support                     = "enable"
  vpn_ecmp_support                = "enable"

}

#vpn
resource "aws_vpn_connection" "vpn_conn-1" {
  customer_gateway_id = aws_customer_gateway.cgw_1.id
  transit_gateway_id  = aws_ec2_transit_gateway.tgw.id
  static_routes_only  = true
  
  #tunnel1_inside_cidr   = var.vpn_tunnel1_inside_cidr-1
  #tunnel2_inside_cidr   = var.vpn_tunnel2_inside_cidr-1
  type = aws_customer_gateway.cgw_1.type

  tunnel1_startup_action = "start"

  tunnel2_startup_action = "start"


  # tunnel1_enable_tunnel_lifecycle_control = true
  # tunnel2_enable_tunnel_lifecycle_control = true
}
