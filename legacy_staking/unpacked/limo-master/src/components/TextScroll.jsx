import { Container } from "react-bootstrap";
import Marquee from "react-fast-marquee";

const TextScroll = () => {
  return (
    <Container className="text-scroll-container">
      <Marquee gradient={false} pauseOnHover={true} speed={20}>
      MIR coin is going up, trading is fast and trend is upword. Market is
        going really fast. Invest in MIR.
      </Marquee>
    </Container>
  );
};

export default TextScroll;
