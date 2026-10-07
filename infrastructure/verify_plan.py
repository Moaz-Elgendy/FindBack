"""Check the reviewed deployment plan without printing keys or state contents."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
plan = json.loads(subprocess.check_output([
    'terraform', f'-chdir={root}', 'show', '-json', 'deployment.tfplan'
]))
changes = [c for c in plan['resource_changes'] if c['mode'] == 'managed']
assert len(changes) == 11, f'Unexpected resource count: {len(changes)}'
assert all(c['change']['actions'] == ['create'] for c in changes), 'Plan must only create new resources'
resources = {c['address']: c['change']['after'] for c in changes}
assert plan['variables']['aws_region']['value'] == 'eu-west-1'
sg = resources['aws_security_group.app']
assert {r['from_port'] for r in sg['ingress']} == {22, 80, 443}
ssh = next(r for r in sg['ingress'] if r['from_port'] == 22)
assert ssh['cidr_blocks'] == [plan['variables']['ssh_allowed_cidr']['value']]
assert ssh['cidr_blocks'] != ['0.0.0.0/0']
assert all(r['from_port'] == r['to_port'] for r in sg['ingress'])
instance = resources['aws_instance.app']
assert resources['aws_subnet.public']['map_public_ip_on_launch'] is False
assert instance['metadata_options'][0]['http_tokens'] == 'required'
assert instance['root_block_device'][0]['encrypted'] is True
assert instance['root_block_device'][0]['volume_size'] == plan['variables']['disk_size_gb']['value']
acl = resources['aws_network_acl.public']
assert {r['from_port'] for r in acl['ingress']} == {22, 80, 443, 1024}
assert resources['aws_eip.app']['domain'] == 'vpc'
print('PASS: 11 creations only; eu-west-1; restricted SSH; web ports only; encrypted disk; IMDSv2; return-traffic ACL; static IP')
