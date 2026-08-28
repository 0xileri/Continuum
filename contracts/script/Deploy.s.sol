// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {ContinuumScoreRegistry} from "../src/ContinuumScoreRegistry.sol";

/// @notice Deploys ContinuumScoreRegistry. §9 Day 3: testnet first, then mainnet.
///
/// @dev The parameters mirror config.py so the on-chain rules and the off-chain engine cannot
///      disagree about what a cooldown is:
///        cooldownSeconds  = RESCORE_COOLDOWN_HOURS (6h)  * 3600
///        maxRateChangeBps = MAX_RATE_CHANGE_BPS_PER_UPDATE (50)
///
///      Testnet (free, iterate here):
///        forge script script/Deploy.s.sol:Deploy \
///          --rpc-url https://evmrpc-testnet.0g.ai --broadcast -vvvv
///
///      Mainnet (spends real 0G — read Section 12's sequencing note first):
///        forge script script/Deploy.s.sol:Deploy \
///          --rpc-url https://evmrpc.0g.ai --broadcast -vvvv
///
///      Both read OG_PRIVATE_KEY from the environment. Never put a key in this file or in any
///      committed .env — .gitignore already excludes .env, and the deploy script deliberately has
///      no key default to fall back to.
contract Deploy is Script {
    uint64 constant COOLDOWN_SECONDS = 6 hours;
    uint16 constant MAX_RATE_CHANGE_BPS = 50;

    function run() external returns (ContinuumScoreRegistry registry) {
        uint256 pk = vm.envUint("OG_PRIVATE_KEY");
        address deployer = vm.addr(pk);

        console.log("chain id      ", block.chainid);
        console.log("deployer      ", deployer);
        console.log("balance (wei) ", deployer.balance);

        require(
            block.chainid == 16602 || block.chainid == 16661,
            "not an 0G chain: expected 16602 (Galileo testnet) or 16661 (mainnet)"
        );

        vm.startBroadcast(pk);
        registry = new ContinuumScoreRegistry(COOLDOWN_SECONDS, MAX_RATE_CHANGE_BPS);
        vm.stopBroadcast();

        console.log("registry      ", address(registry));
        console.log("cooldown (s)  ", COOLDOWN_SECONDS);
        console.log("rate cap (bps)", MAX_RATE_CHANGE_BPS);
        console.log("");
        console.log("Record it for the engine:");
        console.log("  export CONTINUUM_REGISTRY_ADDRESS=%s", address(registry));
        console.log(
            block.chainid == 16661
                ? "  explorer: https://chainscan.0g.ai/address/"
                : "  explorer: https://chainscan-galileo.0g.ai/address/",
            address(registry)
        );
    }
}

/// @notice Authorises an additional scorer address after deployment.
/// @dev Split from Deploy because it is a privileged operation on a live contract and should be a
///      deliberate, separately-reviewed transaction rather than something that rides along with a
///      deployment nobody re-read.
contract AuthorizeScorer is Script {
    function run() external {
        uint256 pk = vm.envUint("OG_PRIVATE_KEY");
        address registry = vm.envAddress("CONTINUUM_REGISTRY_ADDRESS");
        address scorer = vm.envAddress("CONTINUUM_SCORER_ADDRESS");

        vm.startBroadcast(pk);
        ContinuumScoreRegistry(registry).setAuthorizedScorer(scorer, true);
        vm.stopBroadcast();

        console.log("authorized %s on %s", scorer, registry);
    }
}
