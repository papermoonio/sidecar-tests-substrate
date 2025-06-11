#!/usr/bin/env python3

import requests
import json
import logging
import argparse
import sys
import time
from typing import Dict, Any, Optional, List, Tuple
from substrateinterface import SubstrateInterface
from dataclasses import dataclass
import ssl

@dataclass
class TestConfig:
    """Configuration for the test suite"""
    sidecar_endpoint: str
    substrate_endpoint: str
    log_level: str
    num_blocks_to_test: int = 5
    retry_attempts: int = 3

class SubstrateSidecarTester:
    """Test suite to compare Substrate RPC data with Sidecar API responses"""
    
    def __init__(self, config: TestConfig):
        self.config = config
        self.logger = self._setup_logger()
        self.substrate = None
        self.sidecar_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'
        }
        
    def _setup_logger(self) -> logging.Logger:
        """Setup logging configuration"""
        logging.basicConfig(
            format='%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            level=getattr(logging, self.config.log_level.upper(), logging.INFO)
        )
        return logging.getLogger(__name__)
    
    def _connect_substrate(self) -> bool:
        """Establish connection to Substrate node"""
        try:
            self.substrate = SubstrateInterface(
                url=self.config.substrate_endpoint,
                ss58_format=42,  # Generic Substrate format
                type_registry_preset='substrate-node-template',
                # Add SSL verification bypass
                ws_options={
                    'sslopt': { 'cert_reqs': ssl.CERT_NONE  } # This disables SSL verification
                }
            )
            self.logger.info(f"Connected to Substrate node at {self.config.substrate_endpoint}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Substrate node: {e}")
            return False
    
    def _fetch_sidecar_data(self, endpoint: str) -> Tuple[Optional[Dict], Optional[str]]:
        """Fetch data from Sidecar API with retry logic"""
        url = f"{self.config.sidecar_endpoint}{endpoint}"
        
        for attempt in range(self.config.retry_attempts):
            try:
                if attempt > 0:
                    time.sleep(2)
                    
                self.logger.debug(f"Fetching from Sidecar: {url} (attempt {attempt + 1})")
                response = requests.get(url, headers=self.sidecar_headers, timeout=30)
                
                if response.status_code == 200:
                    return response.json(), None
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    if attempt == self.config.retry_attempts - 1:
                        return None, error_msg
                        
            except Exception as e:
                if attempt == self.config.retry_attempts - 1:
                    return None, str(e)
                    
        return None, "Max retries exceeded"
    
    def _fetch_substrate_rpc(self, method: str, params: List = None) -> Tuple[Optional[Any], Optional[str]]:
        """Fetch data directly from Substrate RPC"""
        try:
            if params is None:
                params = []
            result = self.substrate.rpc_request(method, params)
            return result.get('result'), None
        except Exception as e:
            return None, str(e)
    
    def _check_block(self, block_identifier: str) -> bool:
        """Compare block data between Substrate RPC and Sidecar API, including transactions"""
        self.logger.info(f"Testing block data comparison for block: {block_identifier}")
        
        # Fetch block from Sidecar
        sidecar_data, sidecar_error = self._fetch_sidecar_data(f"/blocks/{block_identifier}")
        if sidecar_error:
            self.logger.error(f"Failed to fetch block from Sidecar: {sidecar_error}")
            return False
        
        block_hash = sidecar_data.get('hash')
        if not block_hash:
            self.logger.error("Block hash not found in Sidecar response")
            return False
        
        # Fetch the same block from Substrate RPC
        rpc_block, rpc_error = self._fetch_substrate_rpc("chain_getBlock", [block_hash])
        if rpc_error:
            self.logger.error(f"Failed to fetch block from RPC: {rpc_error}")
            return False
        
        all_passed = True
        
        # Compare basic block information
        comparisons = [
            ("Block Number", int(sidecar_data.get('number')), int(rpc_block['block']['header']['number'], 16)),
            ("Parent Hash", sidecar_data.get('parentHash'), rpc_block['block']['header']['parentHash']),
            ("State Root", sidecar_data.get('stateRoot'), rpc_block['block']['header']['stateRoot']),
            ("Extrinsics Root", sidecar_data.get('extrinsicsRoot'), rpc_block['block']['header']['extrinsicsRoot']),
            ("Extrinsics count", len(sidecar_data.get('extrinsics', [])), len(rpc_block['block']['extrinsics'])),
        ]

        # Check extrinsicts and signatures match
        for i, sidecar_ext in enumerate(sidecar_data.get('extrinsics', [])):
            
            extrinsic_rpc = rpc_block['block']['extrinsics'][i]
            decoded_ext = self.substrate.decode_scale(
                type_string ='Extrinsic',
                scale_bytes = extrinsic_rpc
            )

            rpc_call_module = decoded_ext['call']['call_module']
            rpc_call_function = decoded_ext['call']['call_function']
            
            sidecar_method = sidecar_ext.get('method', {})
            sidecar_pallet = sidecar_method.get('pallet', 'unknown')
            sidecar_method_name = sidecar_method.get('method', 'unknown')
            
            comparisons.append(("Extrinsics - Pallet", sidecar_pallet.lower(), rpc_call_module.lower().replace("_", "")))
            comparisons.append(("Extrinsics - Method", sidecar_method_name.lower(), rpc_call_function.lower().replace("_", "")))
            
            sidecar_signature = sidecar_ext.get('signature')
            if sidecar_signature:
                sidecar_signer = sidecar_signature.get('signer', {}).get('id', 'unknown')
                rpc_signer = extrinsic_rpc['signature']['signer']['address']
                comparisons.append(("Extrinsics - Signer", sidecar_signer, rpc_signer))            

        for field_name, sidecar_value, rpc_value in comparisons:
            if sidecar_value == rpc_value:
                self.logger.debug(f"  ✓ {field_name}: {rpc_value}")
            else:
                self.logger.error(f"  ✗ {field_name}: Mismatch - Sidecar: {sidecar_value}, RPC: {rpc_value}")
                all_passed = False

        if all_passed:
            self.logger.info(f"  ✓ Block {block_identifier} validation passed")
        else:
            self.logger.error(f"  ✗ Block {block_identifier} validation failed")
        
        return all_passed
    
    def test_node_version(self) -> bool:
        """Compare chain information between Substrate RPC and Sidecar API"""
        self.logger.info("Testing Node info comparison")
        
        # Fetch node version from RPC
        rpc_node_version, version_error = self._fetch_substrate_rpc("system_version")
        if version_error:
            self.logger.error(f"Failed to fetch node version from RPC: {version_error}")
            return False
        
        # Fetch node version from Sidecar
        sidecar_node_version, sidecar_error = self._fetch_sidecar_data("/node/version")
        if sidecar_error:
            self.logger.error(f"Failed to fetch node version from Sidecar: {sidecar_error}")
            return False
        
        if sidecar_node_version.get('clientVersion') == rpc_node_version:
            self.logger.info(f"  ✓ Node Version: {rpc_node_version}")
        else:
            self.logger.error(f"  ✗ Node Version: Mismatch - Sidecar: {sidecar_node_version.get('clientVersion')}, RPC: {rpc_node_version}")
            return False
        
        return True

    def test_runtime_version(self) -> bool:
        """Compare runtime version between Substrate RPC and Sidecar API"""
        self.logger.info("Testing runtime version comparison")
        
        # Fetch from Sidecar
        sidecar_data, sidecar_error = self._fetch_sidecar_data("/runtime/spec")
        if sidecar_error:
            self.logger.error(f"Failed to fetch runtime spec from Sidecar: {sidecar_error}")
            return False
        
        # Fetch from Substrate RPC
        rpc_data, rpc_error = self._fetch_substrate_rpc("state_getRuntimeVersion")
        if rpc_error:
            self.logger.error(f"Failed to fetch runtime version from RPC: {rpc_error}")
            return False
        
        # Compare key fields
        comparisons = [
            ("Spec Name", sidecar_data.get('specName'), rpc_data.get('specName')),
            ("Spec Version", str(sidecar_data.get('specVersion')), str(rpc_data.get('specVersion'))),
            ("Transaction Version", str(sidecar_data.get('transactionVersion')), str(rpc_data.get('transactionVersion'))),
        ]
        
        all_passed = True
        for field_name, sidecar_value, rpc_value in comparisons:
            if sidecar_value == rpc_value:
                self.logger.info(f"  ✓ {field_name}: {sidecar_value}")
            else:
                self.logger.error(f"  ✗ {field_name}: Mismatch - Sidecar: {sidecar_value}, RPC: {rpc_value}")
                all_passed = False
        
        return all_passed
    
    def test_head_block(self) -> bool:
        """Compare block data between Substrate RPC and Sidecar API"""
        self.logger.info(f"Testing head block")
        
        # Fetch from Sidecar
        sidecar_data, sidecar_error = self._fetch_sidecar_data("/blocks/head")
        if sidecar_error:
            self.logger.error(f"Failed to fetch block from Sidecar: {sidecar_error}")
            return False
    
        # Fetch from Substrate RPC
        rpc_head_hash, rpc_head_error = self._fetch_substrate_rpc("chain_getFinalizedHead")
        if rpc_head_error:
            self.logger.error(f"Failed to fetch block from RPC: {rpc_head_error}")
            return False
        
        # Fetch from Substrate RPC
        rpc_block, rpc_block_error = self._fetch_substrate_rpc("chain_getBlock", [rpc_head_hash])
        if rpc_block_error:
            self.logger.error(f"Failed to fetch block from RPC: {rpc_block_error}")
            return False
        
        # Compare key fields
        comparisons = [
            ("Block Number", int(sidecar_data.get('number')), int(rpc_block['block']['header']['number'], 16)),
            ("Block Hash", sidecar_data.get('hash'), rpc_head_hash),
            ("Parent Hash", sidecar_data.get('parentHash'), rpc_block['block']['header']['parentHash']),
            ("State Root", sidecar_data.get('stateRoot'), rpc_block['block']['header']['stateRoot']),
            ("Extrinsics Root", sidecar_data.get('extrinsicsRoot'), rpc_block['block']['header']['extrinsicsRoot']),
            ("Extrinsics Count", len(sidecar_data.get('extrinsics', [])), len(rpc_block['block']['extrinsics'])),
        ]
        
        all_passed = True
        for field_name, sidecar_value, rpc_value in comparisons:

            if sidecar_value == rpc_value:
                self.logger.info(f"  ✓ {field_name}: {rpc_value}")
            else:
                self.logger.error(f"  ✗ {field_name}: Mismatch - Sidecar: {sidecar_value}, RPC: {rpc_value}")
                all_passed = False
        
        return all_passed
    
    ## hasta aca

    def test_last_n_blocks_transactions(self, num_blocks: int = 20) -> bool:
        """Compare transaction data for the last N blocks between Substrate RPC and Sidecar API"""
        self.logger.info(f"Testing transaction data comparison for last {num_blocks} blocks")
        
        # Get the current head block
        sidecar_head, head_error = self._fetch_sidecar_data("/blocks/head")
        if head_error:
            self.logger.error(f"Failed to fetch head block from Sidecar: {head_error}")
            return False
        
        # Parse head block number
        head_number = int(sidecar_head.get('number'))
        
        all_passed = True
        total_transactions = 0
        total_extrinsics = 0
        
        # Test each of the last N blocks
        for i in range(num_blocks):
            block_number = head_number - i

            self.logger.debug(f"Testing block {block_number}...")
            
            # Test transaction data for this block
            block_passed = self._check_block(block_number)
            if not block_passed:
                all_passed = False
                self.logger.error(f"  ✗ Transaction data test failed for block {block_number}")
                continue
            
            # Gather statistics
            sidecar_block, _ = self._fetch_sidecar_data(f"/blocks/{block_number}")
            if sidecar_block:
                extrinsics = sidecar_block.get('extrinsics', [])
                total_extrinsics += len(extrinsics)
                
                # Count actual transactions (excluding inherents like timestamp, etc.)
                actual_transactions = 0
                for ext in extrinsics:
                    method = ext.get('method', {})
                    pallet = method.get('pallet', '')
                    method_name = method.get('method', '')
                    
                    # Skip common inherent extrinsics
                    if not (pallet == 'timestamp' and method_name == 'set') and \
                       not (pallet == 'parachainSystem' and method_name in ['setValidationData', 'sudo']):
                        actual_transactions += 1
                
                total_transactions += actual_transactions
        
        # Print summary statistics
        self.logger.info(f"  ✓ Tested {num_blocks} blocks")
        self.logger.info(f"  ✓ Total extrinsics: {total_extrinsics}")
        self.logger.info(f"  ✓ Total transactions (excluding inherents): {total_transactions}")
        
        return all_passed

    def run_tests(self) -> bool:
        """Run all comparison tests"""
        self.logger.info("="*80)
        self.logger.info("Starting Substrate-Sidecar Comparison Tests")
        self.logger.info("="*80)
        
        if not self._connect_substrate():
            return False
        
        test_results = []
        
        # Test node info
        test_results.append(("Node Info", self.test_node_version()))

        # Test runtime version
        test_results.append(("Runtime Version", self.test_runtime_version()))
        
        # Test current head block
        test_results.append(("Head Block", self.test_head_block()))

        # Test transaction data for the last n blocks
        test_results.append((f"Last {self.config.num_blocks_to_test} Blocks Transactions", self.test_last_n_blocks_transactions(self.config.num_blocks_to_test)))
        
        # Print summary
        self.logger.info("="*80)
        self.logger.info("Test Results Summary")
        self.logger.info("="*80)
        
        passed_tests = 0
        total_tests = len(test_results)
        
        for test_name, result in test_results:
            status = "PASS" if result else "FAIL"
            icon = "✓" if result else "✗"
            self.logger.info(f"{icon} {test_name}: {status}")
            if result:
                passed_tests += 1
        
        success_rate = (passed_tests / total_tests) * 100 if total_tests > 0 else 0
        self.logger.info("="*80)
        self.logger.info(f"Tests Passed: {passed_tests}/{total_tests} ({success_rate:.1f}%)")
        
        return passed_tests == total_tests

def parse_arguments():
    parser = argparse.ArgumentParser(description="Test - Sidecar API data consistency")
    
    parser.add_argument(
        "-s", "--sidecar-endpoint", 
        default="http://localhost:8080",
        help="Sidecar API endpoint (default: http://localhost:8080)"
    )
    parser.add_argument(
        "-r", "--substrate-endpoint", 
        default="ws://localhost:9944",
        help="Substrate RPC endpoint (default: ws://localhost:9944)"
    )
    parser.add_argument(
        "-l", "--log-level", 
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)"
    )
    parser.add_argument(
        "-n", "--num-blocks", 
        type=int,
        default=5,
        help="Number of recent blocks to test (default: 5)"
    )
    parser.add_argument(
        "--retry-attempts", 
        type=int,
        default=3,
        help="Number of retry attempts for failed requests (default: 3)"
    )
    
    return parser.parse_args()

def main():
    args = parse_arguments()

    config = TestConfig(
        sidecar_endpoint=args.sidecar_endpoint,
        substrate_endpoint=args.substrate_endpoint,
        log_level=args.log_level,
        num_blocks_to_test=args.num_blocks,
        retry_attempts=args.retry_attempts
    )
    
    tester = SubstrateSidecarTester(config)

    try:
        success = tester.run_tests()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nTest execution interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error during test execution: {e}")
        sys.exit(1)

if __name__ == "__main__":
  main()
