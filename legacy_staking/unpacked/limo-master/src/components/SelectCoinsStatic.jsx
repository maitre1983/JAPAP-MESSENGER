import { useState } from "react";
import { Col, Row } from "react-bootstrap";
import Dropdown from "react-bootstrap/Dropdown";
import DropdownButton from "react-bootstrap/DropdownButton";
import { FiChevronRight } from "react-icons/fi";
import LIMOCOIN32 from "./../images/limo-coin32.svg";
import LIMOCOIN from "./../images/limo-coin64.svg";
import PriceCard from "./PriceCard";
import StakingCardStatic from "./StakingCardStatic";

const SelectCoinsStatic = () => {
  const [pairOne, setPairOne] = useState(true);
  const [pairTwo, setPairTwo] = useState(false);
  const [pairThree, setPairThree] = useState(false);
  const [pairFour, setPairFour] = useState(false);

  const stakeHandler3 = () => {
    console.log("3 Months Staking");
  };
  const stakeHandler6 = () => {
    console.log("6 Months Staking");
  };
  const stakeHandler12 = () => {
    console.log("12 Months Staking");
  };
  const unStakeHandler3 = () => {
    console.log("3 Months unStaking");
  };
  const unStakeHandler6 = () => {
    console.log("6 Months unStaking");
  };
  const unStakeHandler12 = () => {
    console.log("12 Months unStaking");
  };

  const coinPairSelectorOne = (e) => {
    e.preventDefault();
    setPairOne(true);
    setPairFour(false);
    setPairThree(false);
    setPairTwo(false);
  };
  const coinPairSelectorTwo = (e) => {
    e.preventDefault();

    setPairFour(false);
    setPairThree(false);
    setPairTwo(true);
    setPairOne(false);
  };
  const coinPairSelectorThree = (e) => {
    e.preventDefault();

    setPairFour(false);
    setPairThree(true);
    setPairTwo(false);
    setPairOne(false);
  };
  const coinPairSelectorFour = (e) => {
    e.preventDefault();
    setPairFour(true);
    setPairThree(false);
    setPairTwo(false);
    setPairOne(false);
  };

  return (
    <>
      {/* <Row className="rm">
        <Col>
          <DropdownButton
            id="dropdown-basic-button"
            className="dropdown-button"
            title="Select Staking Pair"
            name="selectCoin"
          >
            <Dropdown.Item value="one" onClick={coinPairSelectorOne}>
              <img src={LIMOCOIN32} alt="" className="drop-image" />
              <FiChevronRight />
              <img src={LIMOCOIN32} alt="" className="drop-image" />
            </Dropdown.Item>
          </DropdownButton>
        </Col>
      </Row> */}
      {pairOne && (
        <>
          <Row className="row-selection-2">
            <Col md="7" className="row-selection-col1">
              <div>
                <div className="currency-section">
                  <div className="currency-logo-section">
                    <img src={LIMOCOIN} alt="limo coin" width={64} />
                    <p> MIR</p>
                  </div>
                  <span style={{ padding: "20px" }}>
                    <FiChevronRight style={{ fontSize: "20px" }} />
                  </span>
                  <div className="currency-logo-section ">
                    <img src={LIMOCOIN} alt="limo coin" width={64} />
                    <p> MIR</p>
                  </div>
                </div>
                <div>
                  <p className="light"> Early withdrawal fee ... </p>
                </div>
              </div>
            </Col>
            <Col sm="12" md="5" className="total-limo-staked-col">
              <div className="total-limo-staked1">
                <PriceCard
                  title="Total MIR Staked"
                  balance="..."
                  image={LIMOCOIN32}
                />
              </div>
              <div className="total-limo-staked2">
                <PriceCard
                  title="Total MIR Balance"
                  balance="..."
                  image={LIMOCOIN32}
                />{" "}
              </div>
            </Col>
          </Row>

          <Row className="row-selection-3">
            <Col md="4" className="row3-col">
              <StakingCardStatic
                title="3 MONTHS STAKING"
                APY="..."
                amount="..."
                // limoLimit="Min 100k Limo"
                // interest="Early withdrawal fee 2%"
              />
              <w3m-button />
            </Col>
            <Col md="4" className="row3-col">
              <StakingCardStatic
                title="6 MONTHS STAKING"
                APY="..."
                amount="..."
                // limoLimit="Min 100k Limo"
                // interest="Early withdrawal fee 2%"
                btnStake="STAKE NOW"
                btnUnstake="UNSTAKE"
              />
              <w3m-button />
            </Col>
            <Col md="4" className="row3-col">
              <StakingCardStatic
                title="12 MONTHS STAKING"
                APY="..."
                amount="..."
                // limoLimit="Min 100k Limo"
                // interest="Early withdrawal fee 2%"
                btnStake="STAKE NOW"
                btnUnstake="UNSTAKE"
              />
              <w3m-button />
            </Col>
          </Row>
        </>
      )}
    </>
  );
};

export default SelectCoinsStatic;
