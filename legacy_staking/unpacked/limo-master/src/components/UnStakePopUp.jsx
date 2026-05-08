import { COINS } from "@core/constants/coins";
import moment from "moment";
import { useState } from "react";
import Button from "react-bootstrap/Button";
import Modal from "react-bootstrap/Modal";
import OverlayTrigger from "react-bootstrap/OverlayTrigger";
import Tooltip from "react-bootstrap/Tooltip";
import { FiInfo } from "react-icons/fi";
import { GiSandsOfTime } from "react-icons/gi";
import { UnStakeButton } from "./UnStakeButton";

const UnStakeTooltipContent = (props) => {
  const { stake, pool, isConfirm = false, ...rest } = props;
  const apy = pool?.apy || 0;
  const startTime = moment(stake.stakedTime * 1000);
  const endTime = moment(startTime).add(pool?.duration, "seconds");
  const endDate = moment(endTime).format("MMM DD, YYYY");
  const waitingTime = moment.duration(endTime.diff(startTime)).seconds();
  const stakedAmount = parseFloat(stake.stakedAmount);
  const actualReward = parseFloat(stake.reward);
  const rewardToken = pool?.rewardToken || "";
  const stakedToken = pool?.stakeToken || "";

  const stakeCoin = COINS.find((coin) => coin.address == stakedToken);
  const rewardCoin = COINS.find((coin) => coin.address == rewardToken);

  const expectedReward = (stakedAmount * apy) / 100;
  const reward = expectedReward;

  if (isConfirm) {
    return (
      <span className="unstake-popup-text">
        <FiInfo style={{ color: "#fe0000" }} /> Unstake ({stake.stakedAmount}{" "}
        {stakeCoin?.name}) on {endDate} without paying {pool?.earlyUnStakeFee}%
        early unstake fee.
        <br></br>Press “Yes” to unstake by paying {pool?.earlyUnStakeFee}% fee.
      </span>
    );
  }

  if (waitingTime < 1) {
    return (
      <p>
        Unstake {stake.stakedAmount} {stakeCoin?.name} to get {reward}{" "}
        {rewardCoin?.name}
      </p>
    );
  }

  const content = `Unstake ${stake.stakedAmount}  ${stakeCoin?.name} to get ${reward} ${rewardCoin?.name} on ${endDate}`;

  return <p>{content}</p>;
};

const UnStakePopUp = (props) => {
  const { stakedItems, pool } = props;
  const [currentStake, setCurrentStake] = useState(null);
  const [show, setShow] = useState(false);
  const handleClose = () => setShow(false);
  const handleShow = () => setShow(true);
  const renderTooltip = (props) => (
    <Tooltip id="button-tooltip" {...props}>
      Unstake on Mar 20, 2024 without paying 5% early unstake fee.
    </Tooltip>
  );
  return (
    <>
      <Modal
        className="unstake-modal-container"
        show={props.show}
        onHide={props.onHide}
        centered
      >
        <Modal.Header closeButton className="unstaking-madal-header">
          <h2>
            {" "}
            <GiSandsOfTime /> Please select from further unstaking options:
          </h2>
        </Modal.Header>
        <Modal.Body className="unstake-modal-heading">
          <div className="m-2 d-flex flex-wrap unstake-btn-div">
            {stakedItems?.map((stake, index) => {
              const startTime = moment(stake.stakedTime * 1000);
              const endTime = moment(startTime).add(pool?.duration, "seconds");
              const waitingTime = moment
                .duration(endTime.diff(startTime))
                .seconds();

              if (waitingTime < 1) {
                return (
                  <UnStakeButton
                    className="unstake-popup-btn"
                    pool={pool}
                    index={stake.index}
                    stake={stake}
                  >
                    UNSTAKE {stake.stakedAmount}
                  </UnStakeButton>
                );
              }

              return (
                <OverlayTrigger
                  key={index}
                  placement="top"
                  delay={{ show: 250, hide: 400 }}
                  overlay={(props) => (
                    <Tooltip id="button-tooltip" {...props}>
                      <UnStakeTooltipContent stake={stake} pool={pool} />
                    </Tooltip>
                  )}
                >
                  <Button
                    onClick={() => setCurrentStake(stake)}
                    className={`unstake-popup-btn ${
                      stake?.index == currentStake?.index ? "active" : ""
                    } `}
                  >
                    <span> UNSTAKE {stake.stakedAmount} </span>
                  </Button>
                </OverlayTrigger>
              );
            })}
          </div>

          {currentStake?.stakedAmount > 0 && (
            <h3>
              {
                <UnStakeTooltipContent
                  pool={pool}
                  stake={currentStake}
                  isConfirm={true}
                />
              }
            </h3>
          )}
        </Modal.Body>

        <Modal.Footer>
          {currentStake && (
            <div className="unstake-footer-div">
              <UnStakeButton
                pool={pool}
                index={currentStake.index}
                stake={currentStake}
                className="unstake-popup-btn-proceed"
                onSuccess={() => {
                  setTimeout(() => {
                    setCurrentStake(null);
                    props.onHide();
                  }, 300);
                }}
              >
                <span className="proceed"> Yes Proceed</span>
              </UnStakeButton>
              {/* <Button
                onClick={props.onHide}
                variant="danger"
                className="unstake-popup-btn-foo"
              >
                <span>Yes Proceed</span>
              </Button> */}
              <Button
                onClick={props.onHide}
                className="unstake-popup-btn-reject"
              >
                <span className="reject"> Reject</span>
              </Button>
            </div>
          )}
        </Modal.Footer>
      </Modal>
    </>
  );
};

export default UnStakePopUp;
