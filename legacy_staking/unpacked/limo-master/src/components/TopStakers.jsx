import { Formatter } from "@core/services/formatter";
import { useStakeHolderStore } from "@store/StakerStore";
import { Col, Row } from "react-bootstrap";
import BRONZE from "./../images/bronze-01.svg";
import GOLD from "./../images/gold-01.svg";
import LIMOCOIN from "./../images/limo-coin32.svg";
import PURPLE from "./../images/purple-01.svg";
import RED from "./../images/red-01.svg";
import SILVER from "./../images/silver-01.svg";

const STAKERS = [
  { id: 1, WAddress: "0x87878787879982652552", amount: 300, star: GOLD },
  { id: 2, WAddress: "0x87878787879982652552", amount: 400, star: SILVER },
  { id: 3, WAddress: "0x87878787879982652552", amount: 234, star: BRONZE },
  { id: 4, WAddress: "0x87878787879982652552", amount: 432, star: PURPLE },
  { id: 5, WAddress: "0x87878787879982652552", amount: 467, star: RED },
  { id: 6, WAddress: "0x87878787879982652552", amount: 876 },
  { id: 7, WAddress: "0x87878787879982652552", amount: 765 },
  { id: 8, WAddress: "0x87878787879982652552", amount: 678 },
];

const TopStakers = (props) => {
  // const { stakers = [] } = props;
  const stakerStore = useStakeHolderStore();
  const stakers = stakerStore.topStakers;

  return (
    <>
      <Row className="row-stakers-one">
        <Col className="stakers-row-col-one">
          <div className="stakers-head-num">
            <span> #</span>{" "}
          </div>
          <div className="stakers-head-title">
            <h2> Stakers</h2>
          </div>
          <div className="staked-coins-head">
            <img src={LIMOCOIN} alt="limo" height={32} width={32} />

            <h2> Staked</h2>
          </div>
        </Col>
      </Row>
      <div className="stakers-container-inside">
        {stakers.slice(0, 10).map((TopStaker, index) => {
          const fixedStaker = STAKERS.find((s) => s.id === index + 1);
          const star = fixedStaker?.star;
          return (
            <Row className="row-stakers" key={TopStaker.id}>
              <Col className="stakers-row-col">
                <div className="stakers-head-num">
                  <span>
                    {star && (
                      <img src={star} alt="star" width={32} className="stars" />
                    )}

                    {index + 1}
                  </span>
                </div>
                <div className="stakers-head-title-inside">
                  <h3> {TopStaker.address}</h3>
                </div>
                <div className="staked-coins-head">
                  <h3> {Formatter.kFormat(TopStaker.quantity)}</h3>
                </div>
              </Col>
            </Row>
          );
        })}
        {false &&
          STAKERS.map((TopStaker) => {
            return (
              <Row className="row-stakers" key={TopStaker.id}>
                <Col className="stakers-row-col">
                  <div className="stakers-head-num">
                    <span>
                      {TopStaker.id === 1 && (
                        <img
                          src={GOLD}
                          alt="star"
                          width={32}
                          className="stars"
                        />
                      )}
                      {TopStaker.id === 2 && (
                        <img
                          src={SILVER}
                          alt="star"
                          width={32}
                          className="stars"
                        />
                      )}
                      {TopStaker.id === 3 && (
                        <img
                          src={BRONZE}
                          alt="star"
                          width={32}
                          className="stars"
                        />
                      )}
                      {TopStaker.id === 4 && (
                        <img
                          src={PURPLE}
                          alt="star"
                          width={32}
                          className="stars"
                        />
                      )}
                      {TopStaker.id === 5 && (
                        <img
                          src={RED}
                          alt="star"
                          width={32}
                          className="stars"
                        />
                      )}
                      {TopStaker.id}
                    </span>
                  </div>
                  <div className="stakers-head-title-inside">
                    <h3> {TopStaker.WAddress}</h3>
                  </div>
                  <div className="staked-coins-head">
                    <h3> ${TopStaker.amount}K</h3>
                  </div>
                </Col>
              </Row>
            );
          })}
      </div>
    </>
  );
};

export default TopStakers;
