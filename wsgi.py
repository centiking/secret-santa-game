from app import app, socketio

# Expose app for gunicorn - this is what gunicorn looks for
# For Flask-SocketIO, we still use the app instance
application = app

if __name__ == "__main__":
    socketio.run(app)