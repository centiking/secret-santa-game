import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import secrets
import string
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')

# Use threading mode - most reliable for production
socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode='threading',
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1000000,
    allow_upgrades=True,
    http_compression=True,
    compression_threshold=1024
)
# Store game sessions in memory
games = {}

def generate_code():
    """Generate a unique 6-character room code"""
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))

class Game:
    def __init__(self, host_id):
        self.host_id = host_id
        self.players = {}
        self.assignments = {}
        self.guesses = {}
        self.completed = {}
        self.started = False
        
    def add_player(self, sid, name):
        self.players[sid] = {
            'name': name,
            'joined_at': datetime.now()
        }
        self.guesses[name] = []
        
    def remove_player(self, sid):
        if sid in self.players:
            name = self.players[sid]['name']
            del self.players[sid]
            if name in self.guesses:
                del self.guesses[name]
            if name in self.assignments:
                del self.assignments[name]
            if name in self.completed:
                del self.completed[name]
                
    def set_assignment(self, player_name, got_name):
        self.assignments[player_name] = got_name
        
    def make_guess(self, player_name, guessed_santa):
        self.guesses[player_name].append(guessed_santa)
        
        # Find the actual santa
        actual_santa = None
        for santa, recipient in self.assignments.items():
            if recipient == player_name:
                actual_santa = santa
                break
                
        if actual_santa == guessed_santa:
            self.completed[player_name] = {
                'timestamp': datetime.now(),
                'guess_count': len(self.guesses[player_name]),
                'santa': actual_santa
            }
            return True
        return False
    
    def get_leaderboard(self):
        sorted_completed = sorted(
            self.completed.items(),
            key=lambda x: (x[1]['guess_count'], x[1]['timestamp'])
        )
        return [
            {
                'rank': i + 1,
                'player': player,
                'guesses': data['guess_count'],
                'santa': data['santa']
            }
            for i, (player, data) in enumerate(sorted_completed)
        ]
    
    def is_complete(self):
        return len(self.completed) == len(self.players) if self.players else False

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/host')
def host():
    return render_template('host.html')

@app.route('/player')
def player():
    return render_template('player.html')

@app.route('/health')
def health():
    return {'status': 'healthy', 'games': len(games)}, 200

# Socket.IO Events
@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')
    emit('connected', {'message': 'Connected to server'})

@socketio.on('create_game')
def handle_create_game():
    try:
        print(f'[CREATE_GAME] Request from {request.sid}')
        
        room_code = generate_code()
        while room_code in games:
            room_code = generate_code()
        
        games[room_code] = Game(request.sid)
        join_room(room_code)
        
        print(f'[CREATE_GAME] Success - Room: {room_code}')
        emit('game_created', {'room_code': room_code})
        
    except Exception as e:
        print(f'[CREATE_GAME] Error: {str(e)}')
        emit('error', {'message': 'Failed to create game. Please try again.'})

@socketio.on('join_game')
def handle_join_game(data):
    room_code = data['room_code'].upper()
    player_name = data['player_name']
    
    if room_code not in games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = games[room_code]
    
    if game.started:
        emit('error', {'message': 'Game already started'})
        return
    
    # Check for duplicate names
    for player in game.players.values():
        if player['name'] == player_name:
            emit('error', {'message': 'Name already taken'})
            return
    
    game.add_player(request.sid, player_name)
    join_room(room_code)
    
    emit('joined_game', {
        'room_code': room_code,
        'player_name': player_name
    })
    
    # Notify everyone in the room
    player_list = [p['name'] for p in game.players.values()]
    emit('player_list_update', {
        'players': player_list,
        'count': len(player_list)
    }, room=room_code)

@socketio.on('submit_assignment')
def handle_submit_assignment(data):
    room_code = data['room_code']
    player_name = data['player_name']
    got_name = data['got_name']
    
    if room_code not in games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = games[room_code]
    game.set_assignment(player_name, got_name)
    
    all_assigned = len(game.assignments) == len(game.players)
    
    emit('assignment_submitted', {
        'player_name': player_name,
        'total': len(game.players),
        'submitted': len(game.assignments),
        'all_ready': all_assigned
    }, room=room_code)

@socketio.on('start_game')
def handle_start_game(data):
    room_code = data['room_code']
    
    if room_code not in games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = games[room_code]
    
    if request.sid != game.host_id:
        emit('error', {'message': 'Only host can start game'})
        return
    
    if len(game.assignments) != len(game.players):
        emit('error', {'message': 'Not all players have submitted assignments'})
        return
    
    game.started = True
    
    emit('game_started', {
        'message': 'Game has started! Guess who your Secret Santa is!'
    }, room=room_code)

@socketio.on('make_guess')
def handle_make_guess(data):
    room_code = data['room_code']
    player_name = data['player_name']
    guessed_santa = data['guessed_santa']
    
    if room_code not in games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = games[room_code]
    
    if not game.started:
        emit('error', {'message': 'Game not started yet'})
        return
    
    if player_name in game.completed:
        emit('error', {'message': 'You already found your Santa!'})
        return
    
    is_correct = game.make_guess(player_name, guessed_santa)
    
    emit('guess_result', {
        'correct': is_correct,
        'guessed': guessed_santa,
        'attempts': len(game.guesses[player_name])
    })
    
    if is_correct:
        # Broadcast to room
        emit('player_completed', {
            'player': player_name,
            'santa': guessed_santa,
            'attempts': len(game.guesses[player_name]),
            'rank': len(game.completed)
        }, room=room_code)
        
        # Send updated leaderboard
        leaderboard = game.get_leaderboard()
        emit('leaderboard_update', {
            'leaderboard': leaderboard,
            'game_complete': game.is_complete()
        }, room=room_code)

@socketio.on('get_game_state')
def handle_get_game_state(data):
    room_code = data['room_code']
    
    if room_code not in games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = games[room_code]
    player_list = [p['name'] for p in game.players.values()]
    
    emit('game_state', {
        'players': player_list,
        'started': game.started,
        'leaderboard': game.get_leaderboard(),
        'game_complete': game.is_complete()
    })

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')
    
    for room_code, game in list(games.items()):
        if request.sid in game.players:
            player_name = game.players[request.sid]['name']
            game.remove_player(request.sid)
            
            player_list = [p['name'] for p in game.players.values()]
            emit('player_list_update', {
                'players': player_list,
                'count': len(player_list)
            }, room=room_code)
            
            emit('player_disconnected', {
                'player': player_name
            }, room=room_code)
            
            # Clean up empty games
            if len(game.players) == 0:
                del games[room_code]
                print(f'Deleted empty game: {room_code}')
            break

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)