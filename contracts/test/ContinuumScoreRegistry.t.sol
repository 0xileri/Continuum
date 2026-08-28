// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ContinuumScoreRegistry} from "../src/ContinuumScoreRegistry.sol";

/// @notice Tests for §7's registry, concentrating on the two rules §4 and §5.4 say must hold
///         on-chain rather than only in the off-chain engine.
///
/// @dev The cooldown override is the one worth reading. It is the rule most likely to be
///      "simplified" into a symmetric cooldown by someone who has not read §4, and a symmetric
///      cooldown is silently wrong in the one direction that costs a lender money.
contract ContinuumScoreRegistryTest is Test {
    ContinuumScoreRegistry registry;

    address owner = address(this);
    address scorer = address(0xBEEF);
    address stranger = address(0xDEAD);

    uint64 constant COOLDOWN = 6 hours;
    uint16 constant MAX_RATE_CHANGE = 50;

    string constant BORROWER = "brw_01hx8k2m4n";

    function setUp() public {
        registry = new ContinuumScoreRegistry(COOLDOWN, MAX_RATE_CHANGE);
        registry.setAuthorizedScorer(scorer, true);
        vm.warp(1_780_000_000);
    }

    function _publish(int16 score, int16 lo, int16 hi, string memory trigger)
        internal
        returns (bytes32)
    {
        vm.prank(scorer);
        return registry.publishScore(
            BORROWER, score, lo, hi, trigger, bytes32(uint256(1)), bytes32(uint256(2)), true
        );
    }

    // -----------------------------------------------------------------------------
    // Authorisation
    // -----------------------------------------------------------------------------

    function test_onlyAuthorizedScorerMayPublish() public {
        vm.prank(stranger);
        vm.expectRevert(ContinuumScoreRegistry.NotAuthorizedScorer.selector);
        registry.publishScore(BORROWER, 700, 680, 720, "scheduled_daily", 0, 0, true);
    }

    function test_ownerCanRevokeAScorer() public {
        registry.setAuthorizedScorer(scorer, false);
        vm.prank(scorer);
        vm.expectRevert(ContinuumScoreRegistry.NotAuthorizedScorer.selector);
        registry.publishScore(BORROWER, 700, 680, 720, "scheduled_daily", 0, 0, true);
    }

    function test_strangerCannotAuthorizeThemselves() public {
        vm.prank(stranger);
        vm.expectRevert(ContinuumScoreRegistry.NotOwner.selector);
        registry.setAuthorizedScorer(stranger, true);
    }

    // -----------------------------------------------------------------------------
    // Validation
    // -----------------------------------------------------------------------------

    function test_rejectsScoreOutsideTheScale() public {
        vm.prank(scorer);
        vm.expectRevert(abi.encodeWithSelector(ContinuumScoreRegistry.InvalidScore.selector, int16(1001)));
        registry.publishScore(BORROWER, 1001, 900, 1000, "scheduled_daily", 0, 0, true);
    }

    function test_rejectsAPointEstimateOutsideItsOwnInterval() public {
        vm.prank(scorer);
        vm.expectRevert(
            abi.encodeWithSelector(ContinuumScoreRegistry.InvalidInterval.selector, int16(710), int16(720))
        );
        registry.publishScore(BORROWER, 700, 710, 720, "scheduled_daily", 0, 0, true);
    }

    function test_rejectsEmptyBorrowerId() public {
        vm.prank(scorer);
        vm.expectRevert(ContinuumScoreRegistry.EmptyBorrowerId.selector);
        registry.publishScore("", 700, 680, 720, "scheduled_daily", 0, 0, true);
    }

    // -----------------------------------------------------------------------------
    // §4 cooldown, and its one-sided override
    // -----------------------------------------------------------------------------

    function test_firstPublicationAlwaysSucceeds() public {
        _publish(700, 680, 720, "scheduled_daily");
        ContinuumScoreRegistry.ScoreRecord memory rec = registry.latestScore(BORROWER);
        assertEq(rec.scoreNumeric, 700);
        assertEq(rec.publishCount, 1);
    }

    function test_cooldownBlocksAnOrdinaryRepublish() public {
        _publish(700, 680, 720, "scheduled_daily");
        vm.warp(block.timestamp + 1 hours);

        vm.prank(scorer);
        vm.expectRevert(
            abi.encodeWithSelector(ContinuumScoreRegistry.CooldownActive.selector, uint64(5 hours))
        );
        registry.publishScore(BORROWER, 695, 675, 715, "event_anomaly", 0, 0, true);
    }

    function test_cooldownBlocksAnUpgradeToo() public {
        // §4's override is one-sided. Good news waits; that asymmetry is the point.
        _publish(700, 680, 720, "scheduled_daily");
        vm.warp(block.timestamp + 1 hours);

        vm.prank(scorer);
        vm.expectRevert(
            abi.encodeWithSelector(ContinuumScoreRegistry.CooldownActive.selector, uint64(5 hours))
        );
        registry.publishScore(BORROWER, 810, 790, 830, "event_anomaly", 0, 0, true);
    }

    function test_boundaryCrossingDowngradeOverridesTheCooldown() public {
        // §4: "boundary-crossing downgrades always override the cooldown window and publish
        // immediately — a pool should never lend against a stale grade when risk has clearly
        // worsened, even mid-cooldown."
        _publish(700, 680, 720, "scheduled_daily"); // BBB
        vm.warp(block.timestamp + 30 minutes);

        _publish(520, 470, 570, "event_repayment"); // B — several bands down

        ContinuumScoreRegistry.ScoreRecord memory rec = registry.latestScore(BORROWER);
        assertEq(rec.scoreNumeric, 520);
        assertEq(rec.publishCount, 2);
    }

    function test_downgradeInsideTheSameBandStillWaits() public {
        // The override is specifically boundary-crossing. A drift within one letter is the noise
        // the cooldown exists to damp.
        _publish(700, 680, 720, "scheduled_daily"); // BBB, band 690-729
        vm.warp(block.timestamp + 30 minutes);

        vm.prank(scorer);
        vm.expectRevert();
        registry.publishScore(BORROWER, 695, 675, 715, "event_anomaly", 0, 0, true);
    }

    function test_cooldownExpiryReleasesTheUpdate() public {
        _publish(700, 680, 720, "scheduled_daily");
        vm.warp(block.timestamp + COOLDOWN + 1);
        _publish(710, 690, 730, "scheduled_daily");
        assertEq(registry.latestScore(BORROWER).scoreNumeric, 710);
    }

    function test_cooldownRemainingCountsDown() public {
        _publish(700, 680, 720, "scheduled_daily");
        assertEq(registry.cooldownRemaining(BORROWER), COOLDOWN);
        vm.warp(block.timestamp + 2 hours);
        assertEq(registry.cooldownRemaining(BORROWER), COOLDOWN - 2 hours);
        vm.warp(block.timestamp + COOLDOWN);
        assertEq(registry.cooldownRemaining(BORROWER), 0);
    }

    // -----------------------------------------------------------------------------
    // §5.4 circuit breaker
    // -----------------------------------------------------------------------------

    function test_rateMoveIsCappedAtFiftyBps() public {
        _publish(760, 740, 780, "scheduled_daily");
        uint16 first = registry.latestScore(BORROWER).effectiveRateBps;

        vm.warp(block.timestamp + COOLDOWN + 1);
        _publish(430, 400, 460, "event_anomaly"); // a collapse

        uint16 second = registry.latestScore(BORROWER).effectiveRateBps;
        assertLe(second - first, MAX_RATE_CHANGE, "single update moved the rate past the cap");
    }

    function test_rateCapAppliesDownwardToo() public {
        _publish(430, 400, 460, "scheduled_daily");
        uint16 first = registry.latestScore(BORROWER).effectiveRateBps;

        vm.warp(block.timestamp + COOLDOWN + 1);
        _publish(820, 800, 840, "scheduled_daily");

        uint16 second = registry.latestScore(BORROWER).effectiveRateBps;
        assertLe(first - second, MAX_RATE_CHANGE, "single update moved the rate past the cap");
    }

    function test_rateIsPricedAtTheIntervalsPessimisticEnd() public {
        // §5.4: a wider interval must cost more, at the same point score.
        uint16 tight = registry.indicativeRateBps(690);
        uint16 wide = registry.indicativeRateBps(610);
        assertGt(wide, tight, "a wider band did not cost more");
    }

    function test_rateCurveDoublesPerPdo() public {
        uint16 anchor = registry.indicativeRateBps(700);
        uint16 onePdoDown = registry.indicativeRateBps(630);
        uint256 anchorPremium = anchor - registry.POOL_BASE_RATE_BPS();
        uint256 downPremium = onePdoDown - registry.POOL_BASE_RATE_BPS();
        assertEq(downPremium, anchorPremium * 2, "premium did not double across one PDO");
    }

    function test_riskPremiumIsCapped() public {
        uint16 rate = registry.indicativeRateBps(0);
        assertEq(rate, registry.MAX_RISK_PREMIUM_BPS() + registry.POOL_BASE_RATE_BPS());
    }

    // -----------------------------------------------------------------------------
    // Grade ladder — must match config.GRADE_BANDS exactly
    // -----------------------------------------------------------------------------

    function test_gradeLadderMatchesTheOffChainBands() public view {
        assertEq(registry.gradeIndex(950), 0); // AAA
        assertEq(registry.gradeIndex(742), 4); // A-  — the brief's worked example
        assertEq(registry.gradeIndex(700), 5); // BBB
        assertEq(registry.gradeIndex(0), 14); // D
    }

    // -----------------------------------------------------------------------------
    // Registry bookkeeping
    // -----------------------------------------------------------------------------

    function test_borrowerIsEnumeratedOnceRegardlessOfPublishCount() public {
        _publish(700, 680, 720, "scheduled_daily");
        vm.warp(block.timestamp + COOLDOWN + 1);
        _publish(715, 695, 735, "scheduled_daily");
        assertEq(registry.borrowerCount(), 1);
        assertEq(registry.latestScore(BORROWER).publishCount, 2);
    }

    function test_storesTheAttestationAndStorageRefs() public {
        vm.prank(scorer);
        registry.publishScore(
            BORROWER,
            742,
            710,
            768,
            "scheduled_daily",
            bytes32(uint256(0xABC)),
            bytes32(uint256(0xDEF)),
            true
        );
        ContinuumScoreRegistry.ScoreRecord memory rec = registry.latestScore(BORROWER);
        assertEq(rec.computeAttestationRef, bytes32(uint256(0xABC)));
        assertEq(rec.storageRootHash, bytes32(uint256(0xDEF)));
        assertTrue(rec.attested);
        assertEq(rec.triggerReason, "scheduled_daily");
    }

    function test_unattestedScoreIsStoredAsUnattested() public {
        // The registry records the attestation result rather than requiring it — it cannot check
        // a TEE signature it never sees, and pretending otherwise would be the overclaim §11 warns
        // against. A consumer filters on this flag.
        vm.prank(scorer);
        registry.publishScore(BORROWER, 700, 680, 720, "scheduled_daily", 0, 0, false);
        assertFalse(registry.latestScore(BORROWER).attested);
    }

    function testFuzz_publishIsMonotoneInPublishCount(int16 a, int16 b) public {
        a = int16(bound(int256(a), 0, 1000));
        b = int16(bound(int256(b), 0, 1000));
        _publish(a, a, a, "scheduled_daily");
        vm.warp(block.timestamp + COOLDOWN + 1);
        _publish(b, b, b, "scheduled_daily");
        assertEq(registry.latestScore(BORROWER).publishCount, 2);
        assertEq(registry.latestScore(BORROWER).scoreNumeric, b);
    }
}
