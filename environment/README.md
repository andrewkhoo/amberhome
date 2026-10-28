# installation steps

sudo apt-get update
sudo apt-get install screen vim

ssh ubuntu@mindy.peer.net

# install nginx 
ssh -o BatchMode=yes ubuntu@mindy.peer.net 'export DEBIAN_FRONTEND=noninteractive && sudo apt-get update && sudo apt-get upgrade -y && sudo apt-get install -y nginx && sudo systemctl enable --now nginx && sudo systemctl status nginx --no-pager' 2>&1 | tail -n 60

# Insert the rules above the REJECT line (position 4, so they come before it)
sudo iptables -I INPUT 4 -p tcp -m state --state NEW --dport 80 -j ACCEPT
sudo iptables -I INPUT 5 -p tcp -m state --state NEW --dport 443 -j ACCEPT

# Check the order: 80 and 443 should appear before the REJECT line
sudo iptables -L INPUT -n --line-numbers

# Save the rules so they survive a reboot (netfilter-persistent is already installed)
sudo netfilter-persistent save


####
Layer 2: the OCI network rules. Traffic is also filtered before it reaches the server.

Sign in to the OCI Console and open Compute → Instances, then select mindy.
Under Primary VNIC, open the Subnet link.
Open the Security List attached to that subnet, or the Network Security Group on the VNIC if one is used.
Choose Add Ingress Rules and add two rules:
Source CIDR 0.0.0.0/0, IP protocol TCP, destination port 80
Source CIDR 0.0.0.0/0, IP protocol TCP, destination port 443
Save the rules.
