import { useState } from 'react';
import Button from 'react-bootstrap/Button';
import Modal from 'react-bootstrap/Modal';
import checkMarkIcon from '../images/checkmark.svg';

const Notification = props => {
  const [show, setShow] = useState(false);
  const handleClose = () => setShow(false);
  const handleShow = () => setShow(true);

  return (
    <>
      {/* <Button variant='primary' onClick={props.onClick}>
        {props.BigBtn}
      </Button> */}

      <Modal
        className='modal-container'
        show={props.show}
        onHide={props.onHide}
        centered
      >
        {/* <Modal.Header closeButton>
          <Modal.Title>{props.NotiTitle}</Modal.Title>
        </Modal.Header> */}
        <Modal.Body className='modal-heading' closeButton>
          <div className='checkmark-image'>
            <img src={checkMarkIcon} alt='checkMarkIcon' />
          </div>

          <h3>{props.NotiMessage}</h3>
        </Modal.Body>
        <Modal.Footer>
          <Button
            variant='primary'
            onClick={props.onHide}
            className='popup-btn'
          >
            {props.NotiBtnTxt}
          </Button>
          {/* <Button variant='primary' onClick={handleClose}>
            Save Changes
          </Button> */}
        </Modal.Footer>
      </Modal>
    </>
  );
};

export default Notification;
